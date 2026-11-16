"""Paper-ladder persona lifecycle grant through the real owners.

Real Governance application (``services.governance.main:app``), real Persona
owner lifecycle verification (``HttpGovernanceApprovalVerifier`` ->
``ApprovalReader`` -> ``ApprovalEvidence.require_valid``), real Capital owner
application and real Runtime Manager service for the binding reads.  Only the
Governance decision store is the in-memory mirror of ``execute_command`` that
the existing operator tests use (no database in the worker sandbox); no owner
is replaced by a hand-written fake.
"""
from __future__ import annotations

import io
import json
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from services.capital.conftest import _healthy_guard_collaborators  # noqa: F401  (autouse collaborators)
from services.capital.test_service import _binding_payload, _pool_payload, strict_client  # noqa: F401
from services.governance import approval_authority
from services.governance import main as gov_main
from services.governance.paper_approval_scope import (
    DEV_PAPER_APPROVAL_SCOPE,
    DEV_PAPER_PROVISIONER_SUBJECT,
    PersonaPaperBindingVerifier,
)
from services.governance.test_approval_authority_postgres import OperatorMemoryStore, token
from services.persona.write_owner import HttpGovernanceApprovalVerifier, PersistentPersonaOwner, create_app
from services.runtime_manager import RuntimeManagerService
from tests.persona_capital_write_owner.test_persistent_owners import (
    _persona_create_payload,
    _persona_headers,
    _persona_jwt_headers,
)

SECRET, TENANT = "g" * 40, "tenant-dev"
PROPOSER = "persona-enrollment-proposer"
PERSONA, POOL, BINDING = "persona-ladder-1", "pool-ladder-1", "binding-ladder-1"
GRANT_CONTENT = "enabled;persona_lifecycle=paper_ladder;proposer=" + PROPOSER


def _jwt(sub, roles, **extra):
    return {"Authorization": "Bearer " + token(SECRET, sub=sub, roles=roles, tenant_id=TENANT,
                                              iss="iss", aud="aud", **extra)}


GATE = lambda: _jwt(DEV_PAPER_PROVISIONER_SUBJECT, ["automated_gate"], scope=DEV_PAPER_APPROVAL_SCOPE)  # noqa: E731
PROPOSE = lambda key: {**_jwt(PROPOSER, ["approval_proposer"]), "Idempotency-Key": key}  # noqa: E731


class _Response(io.BytesIO):
    def __init__(self, body, status):
        super().__init__(body)
        self.status = status
        self.headers = type("H", (), {"get_content_type": staticmethod(lambda: "application/json")})()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _GovernanceOpener:
    """Carries the Persona owner's real ApprovalReader GET into the real Governance app."""

    def __init__(self, client):
        self.client = client

    def open(self, request, timeout=None):
        path = request.full_url.split("http://gov", 1)[1]
        response = self.client.get(path, headers={"Authorization": request.get_header("Authorization")})
        if response.status_code != 200:
            from urllib.error import HTTPError
            raise HTTPError(request.full_url, response.status_code, "denied", {}, None)
        return _Response(response.content, 200)


@pytest.fixture()
def world(tmp_path, monkeypatch, strict_client):  # noqa: F811
    for name, value in {"JWT_SECRET": SECRET, "JWT_ISSUER": "iss", "JWT_AUDIENCE": "aud"}.items():
        monkeypatch.setenv(f"PANTHEON_GOVERNANCE_{name}", value)
    grant = tmp_path / "grant"
    grant.write_text(GRANT_CONTENT)
    grant.chmod(0o600)
    monkeypatch.setenv("GOVERNANCE_DEV_PAPER_GRANT_FILE", str(grant))
    monkeypatch.setenv("GOVERNANCE_DEV_PAPER_APPROVAL_ENABLED", "true")
    monkeypatch.setenv("PANTHEON_ENV", "dev")
    monkeypatch.setattr(gov_main, "store", OperatorMemoryStore())
    governance = TestClient(gov_main.app)

    # Real Capital owner: an active paper capital binding for the persona.
    capital, headers_for = strict_client
    operator = headers_for(["operator"], "operator-1", TENANT)
    pool = _pool_payload(actor_id="operator-1", actor_role="operator", pool_id=POOL, risk_policy_ref=None,
                         approval_decision_id=None, metadata={"execution_context": "paper"})
    assert capital.post("/api/capital-pools", json=pool, headers=operator).status_code == 201
    binding = _binding_payload(actor_id="operator-1", actor_role="operator", binding_id=BINDING,
                               persona_id=PERSONA, capital_pool_id=POOL, role="paper_owner",
                               allowed_deployment_scope="paper", capital_sleeve_id=None)
    assert capital.post("/api/bindings", json=binding, headers=operator).status_code == 201
    activate = {"actor_id": "operator-1", "actor_role": "operator"}
    assert capital.post(f"/api/bindings/{BINDING}/activate", json=activate, headers=operator).status_code == 200

    # Real Runtime Manager: an active paper runtime binding.
    manager = RuntimeManagerService(store_path=tmp_path / "runtime_bindings.json")
    runtime = manager.deploy({
        "plan_id": "plan-ladder-1", "plan_status": "approved", "target_stage": "paper",
        "artifact_id": "registry-ladder-1", "artifact_version": "1.0.0", "capital_pool_id": POOL,
        "persona_capital_binding_id": BINDING, "persona_capital_binding_status": "active",
        "allowed_deployment_scope": "paper", "loader_checks_passed": True, "runtime_id": "runtime-ladder-1",
        "sponsor_persona_id": PERSONA, "metadata": {"persona_id": PERSONA, "tenant_id": TENANT},
    })
    assert runtime.status in {"active", "running", "ok"}, runtime.status

    verifier = PersonaPaperBindingVerifier(
        read_capital=lambda query: _ok(capital.get(query, headers=operator)),
        read_runtime=lambda query: {"bindings": [b.to_dict() for b in manager.list_by_pool(POOL)]},
    )
    monkeypatch.setattr(gov_main, "_persona_paper_binding_verifier", lambda: verifier)

    # Real Persona owner whose lifecycle authority is Governance's decision.
    monkeypatch.setenv("PERSONA_AUTH_MODE", "permissive")
    monkeypatch.setenv("PERSONA_JWT_SECRET", "persona-test-secret")
    monkeypatch.setattr(approval_authority, "build_opener", lambda *_a, **_k: _GovernanceOpener(governance))
    reader = {"Authorization": _jwt("persona-owner-reader", ["approval_reader"])["Authorization"]}
    persona_owner = PersistentPersonaOwner.from_json_path(tmp_path / "personas.json")
    persona = TestClient(create_app(persona_owner, governance_decision_verifier=HttpGovernanceApprovalVerifier(
        base_url="http://gov", service_token=reader["Authorization"].split(" ", 1)[1])))
    assert persona.post("/api/personas", json=_persona_create_payload(persona_id=PERSONA, tenant_id=TENANT),
                        headers=_persona_headers("operator-persona", "persona.admin")).status_code == 201
    assert persona.patch(f"/api/personas/{PERSONA}/lifecycle",
                         json={"actor_id": "operator-persona", "target_state": "research_only"},
                         headers=_persona_headers("operator-persona", "persona.admin")).status_code == 200
    return dict(governance=governance, persona=persona, owner=persona_owner, store=gov_main.store, manager=manager)


def _ok(response):
    assert response.status_code == 200, response.text
    return response.json()


def _proposal(decision_id, source, target, **overrides):
    expires = (datetime.now(timezone.utc) + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    body = dict(decision_id=decision_id, expected_version=0, tenant_id=TENANT, owner_user_id=PROPOSER,
                target_type="persona_lifecycle_transition", target_id=PERSONA, target_version="v-" + decision_id,
                risk_level="low", persona_id=PERSONA, expires_at=expires,
                subject={"persona_id": PERSONA, "from_state": source, "to_state": target})
    body.update(overrides)
    return body


def _gate_review_and_decide(governance, decision_id, version=1):
    gate = {"actor_role": "automated_gate", "actor_id": DEV_PAPER_PROVISIONER_SUBJECT}
    review = governance.post(f"/api/governance/approvals/{decision_id}/review",
                             json={**gate, "expected_version": version},
                             headers={**GATE(), "Idempotency-Key": "r-" + decision_id})
    if review.status_code != 200:
        return review
    record = governance.get(f"/api/governance/approvals/{decision_id}", headers=GATE()).json()
    return governance.post(
        f"/api/governance/approvals/{decision_id}/decide",
        json={**gate, "expected_version": record["version"], "outcome": "approved",
              "rationale": "Paper enrolment of a provisioned paper persona", "expires_at": record["expires_at"]},
        headers={**GATE(), "Idempotency-Key": "d-" + decision_id})


def _advance(persona, decision_id, target):
    return persona.patch(
        f"/api/personas/{PERSONA}/lifecycle",
        json={"actor_id": "enrol-actor", "target_state": target, "governance_decision_id": decision_id},
        headers=_persona_jwt_headers("enrol-actor", TENANT, "operator"))


def test_gate_decides_both_paper_ladder_steps_and_persona_owner_accepts(world):
    governance, persona = world["governance"], world["persona"]
    for source, target in (("research_only", "consultable"), ("consultable", "paper_owner")):
        decision_id = f"apv-ladder-{target}"
        created = governance.post("/api/governance/approvals", json=_proposal(decision_id, source, target),
                                  headers=PROPOSE("p-" + decision_id))
        assert created.status_code == 201, created.text
        decided = _gate_review_and_decide(governance, decision_id)
        assert decided.status_code == 200, decided.text
        body = decided.json()
        assert body["decision_state"] == "decided" and body["decision"] == "approved"
        # The decision record carries the grant id and the verified bindings.
        refs = {ref["ref_id"]: ref for ref in body["evidence_refs"]}
        grant_id = next(ref for ref in refs if ref.startswith("dev-paper-grant:persona-lifecycle-paper-ladder:"))
        evidence = json.loads(refs[grant_id]["note"])
        assert evidence["capital_binding_id"] == BINDING and evidence["capital_pool_id"] == POOL
        assert evidence["runtime_binding_id"] and evidence["deployment_plan_id"] == "plan-ladder-1"
        assert (evidence["from_state"], evidence["to_state"]) == (source, target)
        advanced = _advance(persona, decision_id, target)
        assert advanced.status_code == 200, advanced.text
    assert world["owner"].get(PERSONA).lifecycle_state == "paper_owner"


@pytest.mark.parametrize("source,target", [("paper_owner", "live_owner"), ("research_only", "frozen"),
                                           ("research_only", "paper_owner")])
def test_other_transitions_are_denied_with_readable_reason(world, source, target):
    governance = world["governance"]
    created = governance.post("/api/governance/approvals", json=_proposal("apv-out", source, target),
                              headers=PROPOSE("p-out"))
    assert created.status_code in (201, 422), created.text
    if created.status_code == 422:
        return  # the proposal itself is rejected by the target subject rules
    denied = _gate_review_and_decide(governance, "apv-out")
    assert denied.status_code == 403 and "covers only" in denied.json()["detail"], denied.text
    assert _advance(world["persona"], "apv-out", target).status_code in (403, 422)
    assert world["owner"].get(PERSONA).lifecycle_state == "research_only"


def test_persona_without_active_paper_runtime_binding_is_denied(world):
    governance = world["governance"]
    for binding in world["manager"].list_by_pool(POOL):
        world["manager"].retire(binding.binding_id)
    assert governance.post("/api/governance/approvals", json=_proposal("apv-nobind", "research_only", "consultable"),
                           headers=PROPOSE("p-nobind")).status_code == 201
    denied = _gate_review_and_decide(governance, "apv-nobind")
    assert denied.status_code == 403 and "paper runtime binding" in denied.json()["detail"], denied.text
    assert _advance(world["persona"], "apv-nobind", "consultable").status_code == 403
    assert world["owner"].get(PERSONA).lifecycle_state == "research_only"


def test_proposal_from_another_principal_is_denied(world):
    governance = world["governance"]
    other = {**_jwt("someone-else", ["approval_proposer"]), "Idempotency-Key": "p-other"}
    assert governance.post("/api/governance/approvals",
                           json=_proposal("apv-other", "research_only", "consultable", owner_user_id="someone-else"),
                           headers=other).status_code == 201
    denied = _gate_review_and_decide(governance, "apv-other")
    assert denied.status_code == 403 and "named enrolment proposer" in denied.json()["detail"], denied.text


def test_grant_without_widening_denies_the_same_enrolment_decision(world, tmp_path, monkeypatch):
    governance = world["governance"]
    assert governance.post("/api/governance/approvals", json=_proposal("apv-plain", "research_only", "consultable"),
                           headers=PROPOSE("p-plain")).status_code == 201
    plain = tmp_path / "plain-grant"
    plain.write_text("enabled")
    plain.chmod(0o600)
    monkeypatch.setenv("GOVERNANCE_DEV_PAPER_GRANT_FILE", str(plain))
    denied = _gate_review_and_decide(governance, "apv-plain")
    assert denied.status_code == 403, denied.text
    assert "does not authorise persona lifecycle" in denied.json()["detail"]


# ---------------------------------------------------------------------------
# Real issuer output + real configured binding verifier (no 503 from missing wiring)
# ---------------------------------------------------------------------------

def _issued_binding_reader_world(world, tmp_path, monkeypatch, strict_client):  # noqa: F811
    """Wire Governance's *configured* verifier to the real Capital and Runtime Manager apps.

    Tokens are the real ``scripts.issue_dev_paper_principals`` output read from the issued token
    files; the owner HTTP hop is carried into the real Capital app and the real Runtime Manager
    Flask app, both verifying the issuer's secret/issuer/audience.
    """
    import importlib
    import sys
    import yaml
    from pathlib import Path
    from scripts import issue_dev_paper_principals as issuer
    from services.governance import paper_approval_scope as scope

    root = Path(__file__).resolve().parents[3]
    env = {"PANTHEON_ENV": "dev", "PANTHEON_DEV_BFF_TENANT_ID": TENANT,
           "PANTHEON_DEV_PAPER_PRINCIPALS_AUTHORIZED": "true", "PANTHEON_DEV_BFF_JWT_SECRET": "s" * 40,
           "PANTHEON_DEV_BFF_JWT_ISSUER": "pantheon-dev", "PANTHEON_DEV_BFF_JWT_AUDIENCE": "bff-operators"}
    issued_root = tmp_path / "issued"
    issuer.refresh_files(issued_root, env)
    token_dir = issued_root / "governance"
    capital, _headers_for = strict_client
    compose = yaml.safe_load((root / "docker-compose.yml").read_text(encoding="utf-8"))["services"]
    reader_default = compose["capital"]["environment"]["CAPITAL_ALLOWED_READER_SERVICES"]
    reader_services = reader_default.split(":-", 1)[1].rstrip("}")
    monkeypatch.setenv("CAPITAL_JWT_SECRET", env["PANTHEON_DEV_BFF_JWT_SECRET"])
    monkeypatch.setenv("CAPITAL_JWT_ISSUER", env["PANTHEON_DEV_BFF_JWT_ISSUER"])
    monkeypatch.setenv("CAPITAL_JWT_AUDIENCE", env["PANTHEON_DEV_BFF_JWT_AUDIENCE"])
    monkeypatch.setenv("CAPITAL_ALLOWED_READER_SERVICES", reader_services)

    for key, value in {"PANTHEON_RUNTIME_AUTH_MODE": "strict",
                       "PANTHEON_RUNTIME_JWT_SECRET": env["PANTHEON_DEV_BFF_JWT_SECRET"],
                       "PANTHEON_RUNTIME_JWT_ISSUER": env["PANTHEON_DEV_BFF_JWT_ISSUER"],
                       "PANTHEON_RUNTIME_JWT_AUDIENCE": env["PANTHEON_DEV_BFF_JWT_AUDIENCE"],
                       "PANTHEON_RUNTIME_BINDING_STORE_PATH": str(tmp_path / "runtime_bindings.json"),
                       "PANTHEON_COMMAND_STATE_FILE": str(tmp_path / "commands.json")}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.syspath_prepend(str(root / "services" / "runtime-manager"))
    monkeypatch.delitem(sys.modules, "main", raising=False)
    runtime_main = importlib.import_module("main")
    runtime_main._svc = None
    runtime_service = runtime_main._get_service()
    for binding in world["manager"].list_all():
        runtime_service.store._bindings[binding.binding_id] = binding
    runtime_service.store._save()
    runtime_client = runtime_main.app.test_client()
    seen: list[tuple[str, str]] = []

    def owner_get(base, token_provider, _timeout, path, label):
        bearer = "Bearer " + token_provider()
        if label == "Capital":
            assert base.startswith("http://capital:8092")
            response = capital.get(path, headers={"Authorization": bearer})
            seen.append((label, str(response.status_code)))
            if response.status_code != 200:
                raise scope.PaperCandidateUnavailable("Capital did not return 200")
            return response.json()
        assert base.startswith("http://runtime-manager:8081")
        response = runtime_client.get(path, headers={"Authorization": bearer})
        seen.append((label, str(response.status_code)))
        if response.status_code != 200:
            raise scope.PaperCandidateUnavailable("Runtime Manager did not return 200")
        return response.get_json()

    monkeypatch.setattr(scope, "_owner_json_get", owner_get)
    owner_env = {
        "GOVERNANCE_CAPITAL_BASE_URL": "http://capital:8092",
        "GOVERNANCE_RUNTIME_MANAGER_BASE_URL": "http://runtime-manager:8081",
        "GOVERNANCE_CAPITAL_SERVICE_TOKEN_FILE": str(token_dir / "GOVERNANCE_CAPITAL_SERVICE_TOKEN"),
        "GOVERNANCE_RUNTIME_MANAGER_SERVICE_TOKEN_FILE": str(token_dir / "GOVERNANCE_RUNTIME_MANAGER_SERVICE_TOKEN"),
    }
    monkeypatch.setattr(gov_main, "_persona_paper_binding_verifier",
                        lambda: scope.configured_persona_paper_binding_verifier(owner_env))
    return seen, runtime_service


def test_configured_binding_readers_with_issued_principals_approve_paper_ladder(
        world, tmp_path, monkeypatch, strict_client):  # noqa: F811
    seen, _ = _issued_binding_reader_world(world, tmp_path, monkeypatch, strict_client)
    governance = world["governance"]
    assert governance.post("/api/governance/approvals", json=_proposal("apv-wired", "research_only", "consultable"),
                           headers=PROPOSE("p-wired")).status_code == 201
    decided = _gate_review_and_decide(governance, "apv-wired")
    assert decided.status_code == 200, decided.text
    assert decided.json()["decision_state"] == "decided" and decided.json()["decision"] == "approved"
    assert ("Capital", "200") in seen and ("Runtime Manager", "200") in seen


def test_configured_binding_readers_deny_persona_without_bindings_never_503(
        world, tmp_path, monkeypatch, strict_client):  # noqa: F811
    seen, runtime_service = _issued_binding_reader_world(world, tmp_path, monkeypatch, strict_client)
    for binding in runtime_service.list_all():
        runtime_service.retire(binding.binding_id)
    governance = world["governance"]
    assert governance.post("/api/governance/approvals", json=_proposal("apv-wired-no", "research_only", "consultable"),
                           headers=PROPOSE("p-wired-no")).status_code == 201
    denied = _gate_review_and_decide(governance, "apv-wired-no")
    assert denied.status_code == 403, denied.text
    assert denied.status_code != 503 and ("Capital", "200") in seen


def test_unconfigured_binding_readers_return_503_reproducing_hosted_failure(world, monkeypatch):
    for key in ("GOVERNANCE_CAPITAL_BASE_URL", "GOVERNANCE_RUNTIME_MANAGER_BASE_URL"):
        monkeypatch.delenv(key, raising=False)
    from services.governance import paper_approval_scope as scope
    monkeypatch.setattr(gov_main, "_persona_paper_binding_verifier",
                        lambda: scope.configured_persona_paper_binding_verifier({}))
    governance = world["governance"]
    assert governance.post("/api/governance/approvals", json=_proposal("apv-unwired", "research_only", "consultable"),
                           headers=PROPOSE("p-unwired")).status_code == 201
    assert _gate_review_and_decide(governance, "apv-unwired").status_code == 503
