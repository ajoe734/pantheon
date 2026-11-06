"""Program approval -> existing decision/dispatch chain (owner side).

Governance is the only seam: ``FakeGovernance`` serves the exact approval shape
``ApprovalReader`` returns (an ``ApprovalEvidence``) and applies the real
``ApprovalEvidence.require_valid``.  The Evolution decision store, dispatch
outbox, program store, router and auth middleware are all real.
"""
from __future__ import annotations

import os
import tempfile
from typing import Any, Dict, Optional

import pytest

_tmp = tempfile.mkdtemp(prefix="evo_program_approval_")
os.environ.setdefault("EVOLUTION_DATA_DIR", _tmp)
os.environ.setdefault("INCIDENT_DATA_DIR", _tmp)

from fastapi.testclient import TestClient  # noqa: E402

from services.evolution import main as evo_main  # noqa: E402
from services.evolution.dispatch_outbox import (  # noqa: E402
    CompensationLedger,
    EvolutionDispatchOutbox,
    build_dispatch_outbox_store,
)
from services.evolution.program_service import (  # noqa: E402
    ProgramApprovalVerifier,
    ProgramService,
    approval_binding,
)
from services.evolution.program_store import JsonProgramStore  # noqa: E402
from services.governance.approval_authority import ApprovalEvidence, ApprovalInvalid  # noqa: E402

TENANT = "tenant-program-approval"
HUMAN = "human-approver"


class FakeGovernance:
    """Seeded governance owner.  ``request`` mirrors a pending ``evolution_proposal``
    request; ``decide`` mirrors the owner recording a human decision."""

    def __init__(self) -> None:
        self.records: Dict[str, Dict[str, Any]] = {}

    def request(self, approval_id: str, binding: Dict[str, Any], **overrides: Any) -> str:
        body = dict(
            decision_id=approval_id, tenant_id=binding["tenant_id"],
            target_type=binding["target_type"], target_id=binding["target_id"],
            target_version=binding["target_version"], proposal_id=binding["proposal_id"],
            proposal_revision=binding["proposal_revision"],
            proposal_content_digest=binding["proposal_content_digest"],
            decision_state="pending", decision=None, actor_id=None, actor_role=None,
            decided_at=None, expires_at="2099-01-01T00:00:00Z", recorded_at=None,
            authority_status=None, controller_record_ref=None, version=1,
            event_id=f"evt-{approval_id}-1", conditions=[], risk_level="low",
            owner_user_id="proposer",
        )
        body.update(overrides)
        self.records[approval_id] = body
        return approval_id

    def decide(self, approval_id: str, outcome: str = "approved", *, actor_id: str = HUMAN,
               actor_role: str = "governance_reviewer", **overrides: Any) -> None:
        record = self.records[approval_id]
        record.update(
            decision_state="decided", decision=outcome, actor_id=actor_id, actor_role=actor_role,
            decided_at="2026-11-05T00:00:00Z", recorded_at="2026-11-05T00:00:00Z",
            authority_status="authoritative", controller_record_ref=f"governance://{approval_id}",
            version=2, event_id=f"evt-{approval_id}-2", **overrides,
        )

    def get(self, approval_id: str) -> ApprovalEvidence:
        if approval_id not in self.records:
            raise ApprovalInvalid("Governance denied exact decision read")
        return ApprovalEvidence.model_validate(self.records[approval_id])

    def verifier(self) -> ProgramApprovalVerifier:
        return ProgramApprovalVerifier(lambda: self)


def binding_for(service: ProgramService, program_id: str, tenant: str = TENANT) -> Dict[str, Any]:
    return service.approval_binding_for(tenant_id=tenant, program_id=program_id)


@pytest.fixture
def governance() -> FakeGovernance:
    return FakeGovernance()


@pytest.fixture
def service(tmp_path, governance) -> ProgramService:
    return ProgramService(JsonProgramStore(str(tmp_path / "programs.json")),
                          approval_verifier=governance.verifier())


def _program(service: ProgramService) -> str:
    created, _ = service.create_program(tenant_id=TENANT, actor_id="op", name="manual program")
    return created["program_id"]


def _submit(service, governance, program_id: str, approval_id: str = "apv-1") -> str:
    governance.request(approval_id, binding_for(service, program_id))
    service.execute_action(tenant_id=TENANT, actor_id="op", program_id=program_id,
                           action_id="submit_evolution_review", payload={"approval_id": approval_id})
    return approval_id


def _approve(service, program_id: str, **kwargs):
    return service.execute_action(tenant_id=kwargs.pop("tenant_id", TENANT), actor_id="appr",
                                  actor_role="approver", program_id=program_id,
                                  action_id="approve_program", **kwargs)


def test_submit_requires_a_governance_request_and_never_invents_an_id(service):
    program_id = _program(service)
    from services.evolution.program_service import ProgramApprovalError
    with pytest.raises(ProgramApprovalError) as exc:
        service.execute_action(tenant_id=TENANT, actor_id="op", program_id=program_id,
                               action_id="submit_evolution_review")
    assert exc.value.code == "approval_missing"
    assert service.get_program(tenant_id=TENANT, program_id=program_id)["status"] == "draft"


def test_submit_binds_tenant_program_revision_and_rejects_unbound_request(service, governance):
    program_id = _program(service)
    wrong = dict(binding_for(service, program_id), target_id="evp-other", proposal_id="evp-other")
    governance.request("apv-wrong", wrong)
    from services.evolution.program_service import ProgramApprovalError
    with pytest.raises(ProgramApprovalError) as exc:
        service.execute_action(tenant_id=TENANT, actor_id="op", program_id=program_id,
                               action_id="submit_evolution_review", payload={"approval_id": "apv-wrong"})
    assert exc.value.code == "approval_program_mismatch"


def test_manual_program_approved_by_human_becomes_active_with_no_run(service, governance):
    program_id = _program(service)
    approval_id = _submit(service, governance, program_id)
    governance.decide(approval_id)
    result, _ = _approve(service, program_id)
    assert result["program_status"] == "active"
    assert result["details"]["approval_id"] == approval_id
    assert result["details"]["work_started"] is False
    assert result["details"]["decisions"] == []
    assert "no work started" in result["details"]["note"]
    assert result["program"]["run_ids"] == []


@pytest.mark.parametrize("case", [
    "missing", "pending", "rejected", "expired", "other_tenant", "other_program", "stale_revision",
])
def test_approve_is_refused_without_valid_human_governance_evidence(service, governance, case):
    from services.evolution.program_service import ProgramApprovalError
    program_id = _program(service)
    approval_id = _submit(service, governance, program_id)
    expected_code = {
        "missing": "approval_missing", "pending": "approval_pending",
        "rejected": "approval_rejected", "expired": "approval_expired",
        "other_tenant": "approval_tenant_mismatch", "other_program": "approval_program_mismatch",
        "stale_revision": "approval_revision_stale",
    }[case]
    if case == "missing":
        del governance.records[approval_id]
    elif case == "pending":
        pass
    elif case == "rejected":
        governance.decide(approval_id, "rejected")
    elif case == "expired":
        governance.decide(approval_id, expires_at="2020-01-01T00:00:00Z")
    elif case == "other_tenant":
        governance.records[approval_id]["tenant_id"] = "tenant-other"
        governance.decide(approval_id)
    elif case == "other_program":
        governance.records[approval_id].update(target_id="evp-other", proposal_id="evp-other")
        governance.decide(approval_id)
    elif case == "stale_revision":
        governance.decide(approval_id)
        service.patch_program_name(tenant_id=TENANT, actor_id="op", program_id=program_id,
                                   name="renamed after review", expected_revision=2)
    with pytest.raises(ProgramApprovalError) as exc:
        _approve(service, program_id)
    assert exc.value.code == expected_code
    assert service.get_program(tenant_id=TENANT, program_id=program_id)["status"] == "under_review"


def test_local_approval_id_in_payload_is_ignored_and_not_generated(service, governance):
    program_id = _program(service)
    approval_id = _submit(service, governance, program_id)
    governance.decide(approval_id)
    from services.evolution.program_service import ProgramApprovalError
    with pytest.raises(ProgramApprovalError) as exc:
        _approve(service, program_id, payload={"approval_id": "app-locally-made"})
    assert exc.value.code == "approval_program_mismatch"


@pytest.mark.parametrize("system_actor", ["evolution-daily-sweep", "evolution-dispatch-worker"])
def test_system_identities_can_never_approve_or_reject_a_program(service, governance, system_actor):
    from services.evolution.program_service import ProgramForbiddenError
    program_id = _program(service)
    approval_id = _submit(service, governance, program_id)
    governance.decide(approval_id)
    for action in ("approve_program", "reject_program"):
        with pytest.raises(ProgramForbiddenError):
            service.execute_action(tenant_id=TENANT, actor_id=system_actor, actor_role="admin",
                                   program_id=program_id, action_id=action, payload={"reason": "x"})
    assert service.get_program(tenant_id=TENANT, program_id=program_id)["status"] == "under_review"


def test_a_system_identity_cannot_be_the_governance_decider(service, governance):
    from services.evolution.program_service import ProgramApprovalError
    program_id = _program(service)
    approval_id = _submit(service, governance, program_id)
    governance.decide(approval_id, actor_id="evolution-dispatch-worker")
    with pytest.raises(ProgramApprovalError) as exc:
        _approve(service, program_id)
    assert exc.value.code == "approval_not_human"


def test_reject_program_requires_reason_and_retires_without_activation(service, governance):
    from services.evolution.program_service import ProgramValidationError
    program_id = _program(service)
    _submit(service, governance, program_id)
    with pytest.raises(ProgramValidationError):
        service.execute_action(tenant_id=TENANT, actor_id="appr", actor_role="approver",
                               program_id=program_id, action_id="reject_program")
    result, _ = service.execute_action(
        tenant_id=TENANT, actor_id="appr", actor_role="approver", program_id=program_id,
        action_id="reject_program", payload={"reason": "not the standard we want"})
    assert result["program_status"] == "retired"
    assert result["details"]["reason"] == "not the standard we want"


def test_reject_program_is_only_valid_from_under_review(service):
    from services.evolution.program_service import ProgramConflictError
    program_id = _program(service)
    with pytest.raises(ProgramConflictError):
        service.execute_action(tenant_id=TENANT, actor_id="appr", actor_role="approver",
                               program_id=program_id, action_id="reject_program",
                               payload={"reason": "draft cannot be rejected"})


def test_allowed_actions_policy_follows_status_and_role(service, governance):
    from services.evolution.program_service import allowed_actions
    program_id = _program(service)
    draft = service.get_program(tenant_id=TENANT, program_id=program_id)
    assert allowed_actions(draft, "operator") == ["submit_evolution_review"]
    _submit(service, governance, program_id)
    review = service.get_program(tenant_id=TENANT, program_id=program_id)
    assert allowed_actions(review, "operator") == []
    assert allowed_actions(review, "approver") == ["approve_program", "reject_program"]
    assert allowed_actions(review, "approver", "evolution-daily-sweep") == []


def test_governance_unconfigured_fails_closed(tmp_path):
    from services.evolution.program_service import ProgramUnavailableError
    bare = ProgramService(JsonProgramStore(str(tmp_path / "programs.json")))
    created, _ = bare.create_program(tenant_id=TENANT, actor_id="op", name="p")
    with pytest.raises(ProgramUnavailableError):
        bare.execute_action(tenant_id=TENANT, actor_id="op", program_id=created["program_id"],
                            action_id="submit_evolution_review", payload={"approval_id": "x"})


# ---------------------------------------------------------------------------
# Linked decisions: the existing review/approve path and the existing outbox
# ---------------------------------------------------------------------------

from services.evolution import dispatch_outbox as outbox_module  # noqa: E402

client = TestClient(evo_main.app)
HEADERS = {"X-Tenant-Id": TENANT}


@pytest.fixture
def app_env(tmp_path, monkeypatch, governance):
    from services.evolution.dispatch_outbox import (
        install_program_approval_resolver, install_program_sibling_resolver,
    )
    monkeypatch.setattr(evo_main, "store", evo_main.EvolutionDecisionStore(
        storage_path=str(tmp_path / "decisions.json")))
    outbox = EvolutionDispatchOutbox(build_dispatch_outbox_store(data_dir=tmp_path))
    monkeypatch.setattr(evo_main, "dispatch_outbox", outbox)
    monkeypatch.setattr(evo_main, "compensation_ledger", CompensationLedger(data_dir=tmp_path))
    monkeypatch.setattr(evo_main.program_service, "_store", JsonProgramStore(str(tmp_path / "p.json")))
    monkeypatch.setattr(evo_main.program_service, "_verifier", governance.verifier())
    monkeypatch.setattr(evo_main, "_program_approval_reader", lambda: governance)
    monkeypatch.setattr(outbox_module, "configured_approval_reader", lambda domain: governance)
    yield {"outbox": outbox, "governance": governance}


def _propose(decision_id: str, *, risk_action: str = "retrain") -> str:
    created = client.post("/api/evolution/proposals", headers=HEADERS, json={
        "decision_id": decision_id, "tenant_id": TENANT, "target_type": "candidate_artifact",
        "target_id": f"artifact-{decision_id}", "target_version": "1.0.0", "action_type": risk_action,
        "rationale": "Rolling drawdown breached the approved expected baseline.",
        "created_by_id": "evolution-controller-01", "linked_incident_id": f"inc-{decision_id}",
    })
    assert created.status_code == 201, created.text
    return decision_id


def _auto_program(decision_ids) -> str:
    created, _ = evo_main.program_service.create_auto_qualified_program(
        tenant_id=TENANT, actor_id="evolution-daily-sweep", name="auto program",
        decision_id=decision_ids[0], qualification={"policy_version": "test"})
    program_id = created["program_id"]
    store = evo_main.program_service._store
    record = dict(store.get(program_id))
    record["source_decision_ids"] = list(decision_ids)
    store._write_for_test(record) if hasattr(store, "_write_for_test") else _force(store, record)
    return program_id


def _force(store, record):
    store.patch_metadata_cas(
        tenant_id=record["tenant_id"], actor_id="test", program_id=record["program_id"],
        idempotency_key=None, request_fingerprint={"seed": record["program_id"]},
        base_snapshot=store.get(record["program_id"]), mutate=lambda base: dict(base, source_decision_ids=record["source_decision_ids"]))


def _post(program_id: str, action: str, **body):
    return client.post(f"/api/evolution/programs/{program_id}/actions/{action}", headers=HEADERS,
                       json={"actor_id": "appr", "actor_role": "approver", **body})


def _submit_http(program_id: str, env) -> str:
    binding = client.get(f"/api/evolution/programs/{program_id}/approval-binding", headers=HEADERS).json()
    approval_id = env["governance"].request(f"apv-{program_id}", binding)
    response = _post(program_id, "submit_evolution_review", payload={"approval_id": approval_id})
    assert response.status_code == 200, response.text
    return approval_id


def test_approving_a_program_moves_each_decision_through_review_approve_and_one_outbox(app_env):
    ids = [_propose("evo-pa-1"), _propose("evo-pa-2")]
    program_id = _auto_program(ids)
    approval_id = _submit_http(program_id, app_env)
    app_env["governance"].decide(approval_id, actor_role="governance_reviewer")
    response = _post(program_id, "approve_program")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["program_status"] == "active"
    assert body["details"]["work_started"] is True
    assert {item["decision_id"] for item in body["details"]["decisions"]} == set(ids)
    for decision_id in ids:
        decision = evo_main.store.get(decision_id)
        assert decision.decision_state == "approved"
        assert decision.approval_decision_id == approval_id
        assert app_env["outbox"].get(TENANT, decision_id) is not None
    assert len(app_env["outbox"].list_all(tenant_id=TENANT)) == len(ids)


def test_a_decision_that_cannot_advance_keeps_the_program_under_review_and_is_named(app_env):
    ids = [_propose("evo-pb-1"), _propose("evo-pb-2")]
    program_id = _auto_program(ids)
    approval_id = _submit_http(program_id, app_env)
    app_env["governance"].decide(approval_id, actor_role="governance_reviewer")
    # Another actor cancels the second decision after the program was submitted.
    cancelled = client.post("/api/evolution/proposals/evo-pb-2/cancel", headers=HEADERS, json={
        "actor_role": "operator", "actor_id": "op", "note": "superseded", "tenant_id": TENANT})
    assert cancelled.status_code == 200, cancelled.text
    response = _post(program_id, "approve_program")
    assert response.status_code == 409
    assert "evo-pb-2" in response.json()["detail"]
    assert client.get(f"/api/evolution/programs/{program_id}", headers=HEADERS).json()["status"] == "under_review"


def test_approve_retry_with_same_idempotency_key_does_not_duplicate_work(app_env):
    ids = [_propose("evo-pc-1")]
    program_id = _auto_program(ids)
    approval_id = _submit_http(program_id, app_env)
    app_env["governance"].decide(approval_id, actor_role="governance_reviewer")
    first = client.post(f"/api/evolution/programs/{program_id}/actions/approve_program",
                        headers={**HEADERS, "Idempotency-Key": "k-approve"},
                        json={"actor_id": "appr", "actor_role": "approver"})
    second = client.post(f"/api/evolution/programs/{program_id}/actions/approve_program",
                         headers={**HEADERS, "Idempotency-Key": "k-approve"},
                         json={"actor_id": "appr", "actor_role": "approver"})
    assert first.status_code == second.status_code == 200
    assert second.json()["idempotent_replay"] is True
    assert len(app_env["outbox"].list_all(tenant_id=TENANT)) == 1


def test_reject_program_rejects_linked_proposed_decisions_so_cooldown_applies(app_env):
    ids = [_propose("evo-pd-1")]
    program_id = _auto_program(ids)
    _submit_http(program_id, app_env)
    response = _post(program_id, "reject_program", reason="below the standard",
                     decision_actor_role="governance_reviewer")
    assert response.status_code == 200, response.text
    assert response.json()["program_status"] == "retired"
    assert evo_main._enum_value(evo_main.store.get("evo-pd-1").decision_state) == "rejected"
    assert app_env["outbox"].list_all(tenant_id=TENANT) == []


def test_approve_program_refused_when_linked_decision_blocked_by_cooldown(app_env):
    """After reject_program moves a decision to rejected, a new decision on the
    same target cannot advance via approve_program while the rejection cooldown holds."""
    created_first = client.post("/api/evolution/proposals", headers=HEADERS, json={
        "decision_id": "evo-cool-1", "tenant_id": TENANT, "target_type": "candidate_artifact",
        "target_id": "artifact-cool-shared", "target_version": "1.0.0", "action_type": "retrain",
        "rationale": "First proposal that will be rejected by program rejection.",
        "created_by_id": "evolution-controller-01", "linked_incident_id": "inc-cool-1",
    })
    assert created_first.status_code == 201, created_first.text
    p1 = _auto_program(["evo-cool-1"])
    _submit_http(p1, app_env)
    rej = _post(p1, "reject_program", reason="rejected in review",
                decision_actor_role="governance_reviewer")
    assert rej.status_code == 200, rej.text

    # Now evo-cool-1 is rejected (not active), so a new proposal on the same target can be proposed,
    # but advancing it via approve_program must be refused by the rejection cooldown.
    created_second = client.post("/api/evolution/proposals", headers=HEADERS, json={
        "decision_id": "evo-cool-2", "tenant_id": TENANT, "target_type": "candidate_artifact",
        "target_id": "artifact-cool-shared", "target_version": "1.0.1", "action_type": "retrain",
        "rationale": "Second proposal on the same target inside the rejection cooldown window.",
        "created_by_id": "evolution-controller-01", "linked_incident_id": "inc-cool-2",
    })
    assert created_second.status_code == 201, created_second.text
    p2 = _auto_program(["evo-cool-2"])
    apv2 = _submit_http(p2, app_env)
    app_env["governance"].decide(apv2, actor_role="governance_reviewer")

    res = _post(p2, "approve_program")
    assert res.status_code == 409, res.text
    detail = res.json()["detail"]
    assert "evo-cool-2" in detail
    assert "cooldown" in detail
    assert "evo-cool-1" in detail
    assert client.get(f"/api/evolution/programs/{p2}", headers=HEADERS).json()["status"] == "under_review"
    assert app_env["outbox"].list_all(tenant_id=TENANT) == []


def test_approve_program_refused_when_linked_decision_blocked_by_single_active_rule(app_env):
    """If a competing active decision exists on the same target when approve_program
    attempts to advance the linked decision, the single-active rule refuses it,
    the program stays under_review, and the response names the decision and reason."""
    d_id = _propose("evo-sa-1")
    program_id = _auto_program([d_id])
    apv = _submit_http(program_id, app_env)
    app_env["governance"].decide(apv, actor_role="governance_reviewer")

    # Seed a competing active decision on the same (tenant_id, target_type, target_id)
    competing = evo_main.EvolutionDecision.from_dict({
        **evo_main.store.get(d_id).to_dict(),
        "decision_id": "evo-sa-competing",
    })
    evo_main.store._decisions["evo-sa-competing"] = competing

    res = _post(program_id, "approve_program")
    assert res.status_code == 409, res.text
    detail = res.json()["detail"]
    assert "evo-sa-1" in detail
    assert "single-active-rule violated" in detail
    assert client.get(f"/api/evolution/programs/{program_id}", headers=HEADERS).json()["status"] == "under_review"
    assert app_env["outbox"].list_all(tenant_id=TENANT) == []


def test_approving_makes_no_runtime_broker_or_capital_call(app_env, monkeypatch):
    import urllib.request
    calls = []
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: calls.append(a) or (_ for _ in ()).throw(
        AssertionError("program approval must not call any downstream service")))
    ids = [_propose("evo-pe-1")]
    program_id = _auto_program(ids)
    approval_id = _submit_http(program_id, app_env)
    app_env["governance"].decide(approval_id, actor_role="governance_reviewer")
    assert _post(program_id, "approve_program").status_code == 200
    assert calls == []
    program = client.get(f"/api/evolution/programs/{program_id}", headers=HEADERS).json()
    assert program["promotions"] == []


def test_program_approval_authorizes_dispatch_execution_gate_only_for_its_own_decisions(app_env):
    ids = [_propose("evo-pf-1")]
    program_id = _auto_program(ids)
    approval_id = _submit_http(program_id, app_env)
    app_env["governance"].decide(approval_id, actor_role="governance_reviewer")
    assert _post(program_id, "approve_program").status_code == 200
    decision = evo_main.store.get("evo-pf-1").to_dict()
    outbox_module.require_execution_approval(decision)  # the active program's approval covers it
    stranger = _propose("evo-pf-stranger")
    evo_main.store.get(stranger)
    other = evo_main.store.get(stranger)
    other.decision_state = evo_main.store.get("evo-pf-1").decision_state
    other.approval_decision_id = approval_id
    with pytest.raises(ApprovalInvalid):
        outbox_module.require_execution_approval(other.to_dict())


def test_qualifier_surfaces_a_program_and_a_human_submit_attaches_the_governance_request(app_env):
    """The sweep has no human credential, so it cannot file the Governance request;
    it surfaces the program under_review and a human submit files and attaches it."""
    from services.evolution.program_service import ProgramApprovalError, ProgramForbiddenError, allowed_actions
    program_id = _auto_program([_propose("evo-pg-1")])
    service = evo_main.program_service
    sweep, _ = service.execute_action(
        tenant_id=TENANT, actor_id="evolution-daily-sweep", actor_role="operator",
        program_id=program_id, action_id="submit_evolution_review")
    assert sweep["program_status"] == "under_review"
    assert sweep["details"]["approval_id"] is None
    # Without an attached request nobody can approve it.
    with pytest.raises(ProgramApprovalError) as exc:
        _approve(service, program_id)
    assert exc.value.code == "approval_missing"
    under_review = service.get_program(tenant_id=TENANT, program_id=program_id)
    assert "submit_evolution_review" in allowed_actions(under_review, "operator", "human")
    assert "submit_evolution_review" not in allowed_actions(under_review, "operator", "evolution-daily-sweep")
    # A system identity can never attach a request; a human can.
    binding = service.approval_binding_for(tenant_id=TENANT, program_id=program_id)
    app_env["governance"].request("apv-attach", binding)
    with pytest.raises(ProgramForbiddenError):
        service.execute_action(tenant_id=TENANT, actor_id="evolution-daily-sweep", program_id=program_id,
                               action_id="submit_evolution_review", payload={"approval_id": "apv-attach"})
    attached, _ = service.execute_action(
        tenant_id=TENANT, actor_id="human", program_id=program_id,
        action_id="submit_evolution_review", payload={"approval_id": "apv-attach"})
    assert attached["details"]["approval_id"] == "apv-attach"
    app_env["governance"].decide("apv-attach", actor_role="governance_reviewer")
    approved, _ = _approve(service, program_id)
    assert approved["program_status"] == "active"


class _FixedReceiptAdapter:
    """The owner's readback: returns the receipt evidence the plane reported, nothing more."""

    kind = "research_run"

    def __init__(self, evidence_by_run: Dict[str, Dict[str, Any]]) -> None:
        self._evidence = evidence_by_run

    def read_receipt(self, run_id: str, *, expected_intent=None):
        from services.evolution.dispatch_receipts import DispatchReceipt

        return DispatchReceipt(
            outcome="succeeded", downstream_kind=self.kind, downstream_ref_id=run_id,
            downstream_status="completed", detail=f"research run {run_id} reported status=completed",
            evidence=self._evidence[run_id],
        )


def test_program_runs_return_the_recorded_outcome_fields_and_null_for_a_run_that_recorded_none(app_env, monkeypatch):
    program_id = _auto_program([_propose("evo-runs-1")])
    for run_id in ("run-recorded", "run-bare"):
        evo_main.program_service.link_run(
            tenant_id=TENANT, decision_id="evo-runs-1", run_id=run_id,
            downstream_kind="research_run", actor_id="evolution-dispatch-worker")
    recorded = {
        "outcome": "candidate_created", "outcome_reason": "candidate_passed_gate_and_beat_baseline",
        "candidate_artifact_id": "art-1-retrain-abc", "session_id": "ts-1",
        "baseline_metrics": {"sharpe_ratio": 0.5, "sharpe_defined": True},
        "candidate_metrics": {"sharpe_ratio": 1.5, "sharpe_defined": True},
        "baseline_job_id": "job-base",
    }
    adapter = _FixedReceiptAdapter({"run-recorded": recorded, "run-bare": {"run_id": "run-bare", "status": "completed"}})
    monkeypatch.setattr(evo_main, "receipt_registry", {"research": adapter})

    items = {i["run_id"]: i for i in client.get(f"/api/evolution/programs/{program_id}/runs", headers=HEADERS).json()["items"]}
    fields = ("outcome", "outcome_reason", "candidate_artifact_id", "baseline_metrics", "candidate_metrics", "session_id")
    assert {f: items["run-recorded"][f] for f in fields} == {f: recorded[f] for f in fields}
    assert "baseline_job_id" not in items["run-recorded"]  # only the six stated fields are added
    assert {f: items["run-bare"][f] for f in fields} == {f: None for f in fields}
    for item in items.values():  # existing fields unchanged
        assert item["detail"] == f"research run {item['run_id']} reported status=completed"
        assert item["source"] == "orchestrator_readback" and item["status"] == "success"


def test_program_run_without_an_adapter_returns_null_outcome_fields(app_env, monkeypatch):
    program_id = _auto_program([_propose("evo-runs-2")])
    evo_main.program_service.link_run(
        tenant_id=TENANT, decision_id="evo-runs-2", run_id="run-orphan",
        downstream_kind="unknown_plane", actor_id="evolution-dispatch-worker")
    item = client.get(f"/api/evolution/programs/{program_id}/runs", headers=HEADERS).json()["items"][0]
    assert item["status"] == "unknown" and item["outcome"] is None and item["candidate_artifact_id"] is None
