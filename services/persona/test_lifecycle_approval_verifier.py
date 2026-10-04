"""Lifecycle decision verification against ApprovalEvidence.require_valid."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from services.governance.approval_authority import ApprovalEvidence, ApprovalReader
from services.persona.write_owner import (
    HttpGovernanceApprovalVerifier,
    PersistentPersonaOwner,
    create_app,
)

ARGS = dict(decision_id="d1", persona_id="per1", tenant_id="t1",
            source_state="consultable", target_state="paper_owner")


def _evidence(**over):
    base = dict(
        decision_id="d1", tenant_id="t1", target_type="persona_lifecycle_transition",
        target_id="per1", target_version="1", decision_state="decided",
        decision="approved", actor_id="dec", actor_role="governance_reviewer",
        decided_at="2026-01-01T00:00:00Z", expires_at="2099-01-01T00:00:00Z",
        conditions=[], controller_record_ref="ref", authority_status="authoritative",
        recorded_at="2026-01-01T00:00:00Z", version=1, event_id="e1",
        risk_level="low", owner_user_id="proposer",
        metadata={"subject": {"persona_id": "per1", "from_state": "consultable",
                              "to_state": "paper_owner"},
                  "approvals": [{"actor_id": "dec", "actor_role": "governance_reviewer"}]},
    )
    base.update(over)
    return ApprovalEvidence.model_validate(base)


def _verify(monkeypatch, evidence, **over):
    monkeypatch.setattr(ApprovalReader, "get", lambda self, _id: evidence)
    verifier = HttpGovernanceApprovalVerifier(base_url="http://gov", service_token="x")
    return verifier.verify_persona_lifecycle_decision(**{**ARGS, **over})


@pytest.mark.parametrize("race_point", ["governance_read", "first_cas"])
def test_lifecycle_http_rejects_state_drift_after_approval_snapshot(
    tmp_path, monkeypatch, race_point
):
    from tests.persona_capital_write_owner.test_persistent_owners import (
        _persona_create_payload,
        _persona_headers,
        _persona_jwt_headers,
    )

    monkeypatch.setenv("PERSONA_AUTH_MODE", "permissive")
    monkeypatch.setenv("PERSONA_JWT_SECRET", "persona-test-secret")
    owner = PersistentPersonaOwner.from_json_path(tmp_path / "personas.json")
    client = TestClient(create_app(owner, governance_decision_verifier=None))
    assert client.post(
        "/api/personas", json=_persona_create_payload(persona_id="per1", tenant_id="t1"),
        headers=_persona_headers("operator-persona", "persona.admin"),
    ).status_code == 201
    assert client.patch(
        "/api/personas/per1/lifecycle",
        json={"actor_id": "operator-persona", "target_state": "research_only"},
        headers=_persona_headers("operator-persona", "persona.admin"),
    ).status_code == 200
    assert client.patch(
        "/api/personas/per1/lifecycle",
        json={"actor_id": "governance-actor", "target_state": "consultable"},
        headers=_persona_headers("governance-actor", "governance_reviewer"),
    ).status_code == 200

    store = owner._records
    original_cas = store.compare_and_set
    changed = False

    def concurrent_transition(persona_id):
        nonlocal changed
        if changed:
            return
        changed = True
        current = store.get(persona_id)
        concurrent = dict(current)
        concurrent["lifecycle_state"] = "paper_owner"
        assert original_cas(persona_id, current, concurrent)[0]

    evidence = _evidence(metadata={
        "subject": {"persona_id": "per1", "from_state": "consultable",
                    "to_state": "frozen"},
        "approvals": [{"actor_id": "dec", "actor_role": "governance_reviewer"}],
    })

    def governance_read(_reader, _decision_id):
        if race_point == "governance_read":
            concurrent_transition("per1")
        return evidence

    monkeypatch.setattr(ApprovalReader, "get", governance_read)
    verifier = HttpGovernanceApprovalVerifier(base_url="http://gov", service_token="x")
    app = create_app(owner, governance_decision_verifier=verifier)
    client = TestClient(app)
    if race_point == "first_cas":
        def race_cas(persona_id, expected, updated):
            concurrent_transition(persona_id)
            return original_cas(persona_id, expected, updated)
        monkeypatch.setattr(store, "compare_and_set", race_cas)

    response = client.patch(
        "/api/personas/per1/lifecycle",
        json={"actor_id": "operator", "target_state": "frozen",
              "governance_decision_id": "d1"},
        headers=_persona_jwt_headers("operator", "t1", "operator"),
    )
    current = owner.get("per1")
    assert response.status_code == 409
    assert current.lifecycle_state == "paper_owner"
    assert "last_lifecycle_governance_decision_id" not in current.metadata


def test_exact_approved_decision_verifies(monkeypatch):
    assert _verify(monkeypatch, _evidence()) is True


@pytest.mark.parametrize("over", [
    {"decision": "rejected"},
    {"revoked_at": "2026-02-01T00:00:00Z"},
    {"expires_at": "2020-01-01T00:00:00Z"},
    {"tenant_id": "other"},
    {"target_type": "rebalance_apply"},
])
def test_invalid_decision_rejected(monkeypatch, over):
    assert _verify(monkeypatch, _evidence(**over)) is False


@pytest.mark.parametrize("over", [
    {"persona_id": "per2"}, {"source_state": "research_only"},
    {"target_state": "live_owner"}, {"tenant_id": "other"},
])
def test_mismatched_binding_rejected(monkeypatch, over):
    assert _verify(monkeypatch, _evidence(), **over) is False


def test_verifier_reads_rotating_token_file_per_call_without_env_fallback(monkeypatch, tmp_path):
    from services.persona.write_owner import build_training_target_approval_verifier

    path = tmp_path / "PERSONA_GOVERNANCE_SERVICE_TOKEN"
    path.write_text("first")
    path.chmod(0o600)
    monkeypatch.setenv("PERSONA_TRAINING_TARGET_GOVERNANCE_BASE_URL", "http://governance:8082")
    monkeypatch.setenv("PERSONA_GOVERNANCE_SERVICE_TOKEN", "stale-env-secret")
    monkeypatch.setenv("PERSONA_GOVERNANCE_SERVICE_TOKEN_FILE", str(path))
    provider = build_training_target_approval_verifier()._token_provider
    assert provider() == "first"
    path.write_text("rotated")
    assert provider() == "rotated"
    path.unlink()
    with pytest.raises(RuntimeError):
        provider()


def test_unconfigured_strict_verifier_is_503_and_compose_wires_it(monkeypatch):
    from pathlib import Path

    import yaml

    from services.persona.write_owner import PersonaAuthorityError, _authenticate_persona_mutation

    for name in ("PERSONA_JWT_SECRET", "PANTHEON_RUNTIME_JWT_SECRET", "PANTHEON_PERSONA_SERVICE_TOKEN", "PERSONA_SERVICE_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PERSONA_AUTH_MODE", "strict")
    with pytest.raises(PersonaAuthorityError) as err:
        _authenticate_persona_mutation("Bearer a.b.c")
    assert (err.value.status_code, err.value.code) == (503, "AUTH_JWT_SECRET_MISSING")

    compose = Path(__file__).resolve().parents[2] / "docker-compose.yml"
    env = yaml.safe_load(compose.read_text())["services"]["persona"]["environment"]
    assert env["PERSONA_AUTH_MODE"].endswith(":-strict}")
    for key in ("PERSONA_JWT_SECRET", "PERSONA_JWT_ISSUER", "PERSONA_JWT_AUDIENCE"):
        assert "PANTHEON_BFF_JWT_" in env[key]
