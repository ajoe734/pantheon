"""Persona paper enrolment under the real issued proposer principal.

Real here: ``scripts/issue_dev_paper_principals`` output (proposer and paper gate
credentials; the proposer token is delivered through the ``*_FILE`` path the
compose file wires), the default ``HttpGovernanceProposer`` transport, the real
Governance app with its decider-not-proposer verification, and the BFF
enrolment steps (world fixture of ``test_persona_paper_enrollment``).  No
credential value is asserted on or printed.
"""
from __future__ import annotations

import io
import urllib.request
import urllib.error
from pathlib import Path

import pytest
import yaml

from scripts import issue_dev_paper_principals as issuer
from services.control_plane.bff.tests import test_persona_paper_enrollment as enrolment_tests
from services.control_plane.bff.tests.test_persona_paper_enrollment import (  # noqa: F401
    GATE,
    GovernanceBridge,
    PERSONA,
    TENANT,
    _complete_paper_admission,
    _persona,
    world,
)
from services.runtime_auth_inbound import encode_jwt_hs256
from services.governance.approval_authority import ApprovalEvidence, ApprovalInvalid, ApprovalReader

REPO = Path(__file__).resolve().parents[4]
SECRET = "issued-proposer-principal-secret-0123456789abcdef"
ISSUER_ENV = {
    "PANTHEON_ENV": "dev",
    "PANTHEON_DEV_BFF_TENANT_ID": "tenant-dev",
    "PANTHEON_DEV_PAPER_PRINCIPALS_AUTHORIZED": "true",
    "PANTHEON_DEV_BFF_JWT_SECRET": SECRET,
    "PANTHEON_DEV_BFF_JWT_ISSUER": "pantheon-dev",
    "PANTHEON_DEV_BFF_JWT_AUDIENCE": "bff-operators",
}
ISSUED_TENANT = "tenant-dev"
GOV_ENV = ("PANTHEON_GOVERNANCE_JWT_SECRET", "PANTHEON_GOVERNANCE_JWT_ISSUER", "PANTHEON_GOVERNANCE_JWT_AUDIENCE")


@pytest.fixture
def tenant_dev(monkeypatch):
    """Run the enrolment world in the tenant the issuer's principals are bound to."""
    from services.control_plane.bff import test_persona_provisioning_coordinator as coordinator_tests

    monkeypatch.setattr(enrolment_tests, "TENANT", ISSUED_TENANT)

    def record_and_store():
        store = coordinator_tests.TrackingStore()
        record, _ = store.reserve(
            tenant_id=ISSUED_TENANT, idempotency_key="create-persona-a",
            request_hash="sha256:persona-request", normalized_name="trader a", persona_id="persona-a",
            request_payload={"name": "Trader A", "requested_by": "operator-a",
                             "mandate": "Paper-only momentum research",
                             "traits": {"risk_appetite": "low"}, "budget": 25000},
        )
        return store, record

    monkeypatch.setattr(enrolment_tests, "_record_and_store", record_and_store)


def _use_issued_principals(world, monkeypatch, tmp_path, *, configure_proposer: bool):
    """Point the real Governance verifier at the issuer's signer; wire the compose env names."""
    issued = issuer.issue_environment(ISSUER_ENV)
    for name, value in zip(GOV_ENV, (SECRET, "pantheon-dev", "bff-operators")):
        monkeypatch.setenv(name, value)
    # The decider is a plain automated_gate JWT for the test tenant: the issued paper
    # grant principal is tenant-dev and registry_entry-only (see evidence.json).
    gate_token = "Bearer " + encode_jwt_hs256(
        {"sub": GATE, "roles": ["automated_gate"], "tenant_id": ISSUED_TENANT, "allowed_tenants": [ISSUED_TENANT],
         "exp": 4102444800, "iss": "pantheon-dev", "aud": "bff-operators"}, secret=SECRET)
    world.gate.token = gate_token

    def read_decision(_reader, decision_id):
        response = world.governance.get(f"/api/governance/approvals/{decision_id}",
                                        headers={"Authorization": gate_token})
        if response.status_code != 200:
            raise ApprovalInvalid("Governance denied exact decision read")
        return ApprovalEvidence.model_validate(response.json())

    monkeypatch.setattr(ApprovalReader, "get", read_decision)
    monkeypatch.setenv("PANTHEON_GOVERNANCE_APPROVAL_API_URL", "http://governance.invalid")
    if configure_proposer:
        token_file = tmp_path / "PANTHEON_PERSONA_ENROLLMENT_PROPOSER_TOKEN"
        token_file.write_text(issued["PANTHEON_PERSONA_ENROLLMENT_PROPOSER_TOKEN"], encoding="utf-8")
        token_file.chmod(0o600)
        monkeypatch.setenv("PANTHEON_PERSONA_ENROLLMENT_PROPOSER_ID",
                           issued["PANTHEON_PERSONA_ENROLLMENT_PROPOSER_ID"])
        monkeypatch.setenv("PANTHEON_PERSONA_ENROLLMENT_PROPOSER_TOKEN_FILE", str(token_file))
    else:
        for name in ("PANTHEON_PERSONA_ENROLLMENT_PROPOSER_ID", "PANTHEON_PERSONA_ENROLLMENT_PROPOSER_TOKEN",
                     "PANTHEON_PERSONA_ENROLLMENT_PROPOSER_TOKEN_FILE"):
            monkeypatch.delenv(name, raising=False)

    def urlopen(request, timeout=None):
        response = world.governance.request(
            request.get_method(), request.full_url.replace("http://governance.invalid", ""),
            content=request.data, headers=dict(request.header_items()),
        )
        if response.is_error:
            raise urllib.error.HTTPError(request.full_url, response.status_code, "governance rejected",
                                         response.headers, io.BytesIO(response.content))
        return io.BytesIO(response.content)

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    return issued


def _enrol(world):
    _complete_paper_admission(world)
    world.activate_runtime()
    record = world.store.get(world.record.tenant_id, world.record.idempotency_key)
    # The default transport: no injected proposer, so the deployed env names are read.
    coordinator = world.coordinator("issued", governance_proposer=None, governance_proposer_actor_id=None,
                                    governance_actor_id=GATE)
    return coordinator.reconcile_paper_enrollment(record).references["paper_enrollment"]


def test_reproduce_without_proposer_configuration_enrolment_fails_closed(tenant_dev, world, monkeypatch, tmp_path):
    _use_issued_principals(world, monkeypatch, tmp_path, configure_proposer=False)
    readback = _enrol(world)
    assert readback["status"] == "refused"
    assert readback["reason"] == "governance_proposer_identity_unconfigured"
    persona = _persona(world)
    assert persona["lifecycle_state"] == "research_only"
    assert "last_lifecycle_governance_decision_id" not in persona["metadata"]


def test_issued_proposer_completes_three_step_enrolment_with_distinct_decider(tenant_dev, world, monkeypatch, tmp_path):
    issued = _use_issued_principals(world, monkeypatch, tmp_path, configure_proposer=True)
    assert issued["PANTHEON_PERSONA_ENROLLMENT_PROPOSER_ID"] != issuer.PAPER_SUBJECT
    readback = _enrol(world)
    assert readback["status"] == "enrolled", readback
    persona = _persona(world)
    assert persona["lifecycle_state"] == "paper_owner"
    assert persona["metadata"]["persona_harness"]["active_version"] == 1
    assert world.module.POOL_STORE.current(PERSONA, ISSUED_TENANT)["pool_version"] == 1
    for step in readback["steps"]["lifecycle"]:
        decision = world.governance.get(
            f"/api/governance/approvals/{step['governance_decision_id']}",
            headers={"Authorization": world.gate.token},
        ).json()
        assert decision["decision"] == "approved"
        assert decision["owner_user_id"] == issuer.ENROLLMENT_PROPOSER_SUBJECT
        assert decision["actor_id"] == GATE and decision["actor_id"] != decision["owner_user_id"]


def test_issued_proposer_cannot_decide_its_own_proposal(tenant_dev, world, monkeypatch, tmp_path):
    issued = _use_issued_principals(world, monkeypatch, tmp_path, configure_proposer=True)
    _enrol(world)
    proposer_header = {"Authorization": "Bearer " + issued["PANTHEON_PERSONA_ENROLLMENT_PROPOSER_TOKEN"]}
    decision_id = _persona(world)["metadata"]["last_lifecycle_governance_decision_id"]
    response = world.governance.post(
        f"/api/governance/approvals/{decision_id}/decision",
        json={"decision": "approved", "actor_id": issuer.ENROLLMENT_PROPOSER_SUBJECT},
        headers=proposer_header,
    )
    assert response.status_code >= 400


def test_compose_wires_enrolment_proposer_names_on_operator_bff_without_values():
    env = yaml.safe_load((REPO / "docker-compose.yml").read_text(encoding="utf-8"))["services"]["operator-bff"]["environment"]
    for name in ("PANTHEON_PERSONA_ENROLLMENT_PROPOSER_ID", "PANTHEON_PERSONA_ENROLLMENT_PROPOSER_TOKEN",
                 "PANTHEON_PERSONA_ENROLLMENT_PROPOSER_TOKEN_FILE"):
        assert str(env[name]).endswith(":-}"), name  # empty default: unconfigured fails closed
