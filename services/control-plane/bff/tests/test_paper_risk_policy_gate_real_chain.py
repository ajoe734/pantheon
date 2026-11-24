"""PAPER-RISK-GATE-PRINCIPAL-END-TO-END-20261124.

Drives the deployed construction end to end with no fake for any of: the coordinator built by
``service._chain_paper_capital_coordinator``, the real ``_PersonaOwnerHttpTransport``, the real Governance
application (issued principals + issued grant content) and the real Capital application.  Only the socket is
replaced: ``urlopen`` dispatches each request to the owner's ASGI app.  The earlier tests (#7783, #7794) built their
own coordinator with the gate identity, so they never exercised the deployed construction.
"""
from __future__ import annotations

import email.message
import importlib
import io
import json
import sys
import tempfile
import urllib.error
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from scripts import issue_dev_paper_principals as issuer
from services.control_plane.bff.persona_provisioning_coordinator import (
    DraftResult,
    PersonaProvisioningCoordinator,
)
from services.control_plane.bff.personas import service

TENANT, PERSONA, POOL = "tenant-dev", "persona-paper-001", "pool-persona-paper-gate-e2e"
CAPITAL_SECRET = "capital-secret-0123456789-abcdefghijkl"
GATE = "pantheon-dev-paper-provisioner"
PROPOSER = "pantheon-dev-persona-enrollment-proposer"
LEGACY_V1 = {"risk_policy_id": f"rp-{POOL}", "version": "1.0", "status": "active", "allowed_stages": ["paper"],
             "gross_limit": 2.0, "net_limit": 2.0, "max_leverage": 2.0}
DRAFT = {"risk_policy_id": f"rp-{POOL}", "version": "2.0", "status": "active", "allowed_stages": ["paper"],
         "gross_limit": 0.1, "net_limit": 0.1, "max_leverage": 0.1,
         "max_single_name_weight": 0.05, "min_cash_reserve_pct": 0.5}


class _Response(io.BytesIO):
    """The slice of ``http.client.HTTPResponse`` the transports read."""

    status = 200

    def getcode(self) -> int:
        return 200

    @property
    def headers(self) -> email.message.Message:
        message = email.message.Message()
        message["Content-Type"] = "application/json"
        return message


@pytest.fixture
def world(tmp_path, monkeypatch, request):
    pool_policy = getattr(request, "param", None) or LEGACY_V1
    from services.governance import main as gov_main
    from services.governance.test_approval_authority_postgres import OperatorMemoryStore

    secret = "g" * 40
    issued = issuer.issue_environment({
        "PANTHEON_ENV": "dev", "PANTHEON_DEV_BFF_TENANT_ID": TENANT,
        "PANTHEON_DEV_PAPER_PRINCIPALS_AUTHORIZED": "true", "PANTHEON_DEV_BFF_JWT_SECRET": secret,
        "PANTHEON_DEV_BFF_JWT_ISSUER": "iss-test", "PANTHEON_DEV_BFF_JWT_AUDIENCE": "aud-test"})
    grant = tmp_path / "grant"
    grant.write_text(issuer.GOVERNANCE_GRANT_CONTENT)
    grant.chmod(0o600)
    for name, value in {"JWT_SECRET": secret, "JWT_ISSUER": "iss-test", "JWT_AUDIENCE": "aud-test"}.items():
        monkeypatch.setenv(f"PANTHEON_GOVERNANCE_{name}", value)
    monkeypatch.setenv("GOVERNANCE_DEV_PAPER_GRANT_FILE", str(grant))
    monkeypatch.setenv("GOVERNANCE_DEV_PAPER_APPROVAL_ENABLED", "true")
    monkeypatch.setenv("PANTHEON_ENV", "dev")
    monkeypatch.setenv("CAPITAL_RISK_POLICY_DIR", str(tmp_path / "no-policies"))
    monkeypatch.setattr(gov_main, "store", OperatorMemoryStore())
    monkeypatch.setattr(gov_main, "verify_paper_candidate_for_decision", lambda *a, **k: None)
    monkeypatch.setattr(gov_main, "_paper_registry_reader", lambda: None)
    gov = TestClient(gov_main.app)

    cap_dir = tempfile.mkdtemp(prefix="capital_gate_e2e_")
    for key, value in {"CAPITAL_DATA_DIR": cap_dir, "PANTHEON_GOVERNANCE_DATA_DIR": cap_dir,
                       "CAPITAL_STORE_BACKEND": "json", "CAPITAL_AUDIT_BACKEND": "jsonl",
                       "CAPITAL_AUTH_DISABLED": "false", "CAPITAL_AUTH_MODE": "strict",
                       "CAPITAL_JWT_SECRET": CAPITAL_SECRET,
                       "PANTHEON_CAPITAL_JWT_SECRET": CAPITAL_SECRET,
                       "CAPITAL_ALLOWED_CALLER_SERVICES": service.PERSONA_OWNER_SERVICE_ACTOR_ID}.items():
        monkeypatch.setenv(key, value)
    sys.modules.pop("services.capital.main", None)
    cap_main = importlib.reload(importlib.import_module("services.capital.main"))
    cap = TestClient(cap_main.app)

    monkeypatch.setenv("CAPITAL_GOVERNANCE_BASE_URL", "http://governance.test")
    monkeypatch.setenv("CAPITAL_GOVERNANCE_SERVICE_TOKEN", issued["CAPITAL_GOVERNANCE_SERVICE_TOKEN"])
    monkeypatch.setenv("PANTHEON_GOVERNANCE_APPROVAL_API_URL", "http://governance.test")
    monkeypatch.setenv("PANTHEON_CAPITAL_API_URL", "http://capital.test")
    monkeypatch.setenv("PANTHEON_PERSONA_GOVERNANCE_SERVICE_TOKEN", issued["PANTHEON_PERSONA_GOVERNANCE_SERVICE_TOKEN"])
    monkeypatch.setenv("PANTHEON_PERSONA_GOVERNANCE_ACTOR_ID", issued["PANTHEON_PERSONA_GOVERNANCE_ACTOR_ID"])
    monkeypatch.setenv("PANTHEON_PERSONA_ENROLLMENT_PROPOSER_ID", PROPOSER)
    monkeypatch.setenv("PANTHEON_PERSONA_ENROLLMENT_PROPOSER_TOKEN", issued["PANTHEON_PERSONA_ENROLLMENT_PROPOSER_TOKEN"])
    monkeypatch.setenv("PANTHEON_BFF_JWT_SECRET", CAPITAL_SECRET)
    monkeypatch.delenv("OPENCLAW_GATEWAY_URL", raising=False)

    faults: dict[str, Any] = {}
    seen: list[tuple[str, str]] = []

    def urlopen(request, timeout=None, **_kw):
        url = request.full_url
        owner, _, path = url.partition(".test")
        owner = owner.rsplit("//", 1)[-1]
        path = path or "/"
        seen.append((request.get_method(), path))
        for suffix, fault in list(faults.items()):
            if path.endswith(suffix) and request.get_method() == "POST":
                if isinstance(fault, BaseException):
                    raise fault
                raise urllib.error.HTTPError(url, fault[0], "fault", email.message.Message(),
                                             io.BytesIO(json.dumps({"detail": fault[1]}).encode()))
        client = {"governance": gov, "capital": cap}[owner]
        headers = {k: v for k, v in request.header_items()}
        resp = client.request(request.get_method(), path, content=request.data, headers=headers)
        if resp.status_code >= 400:
            raise urllib.error.HTTPError(url, resp.status_code, "error", email.message.Message(),
                                         io.BytesIO(resp.content))
        return _Response(resp.content)

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    from services.governance import approval_authority
    monkeypatch.setattr(approval_authority, "build_opener",
                        lambda *a, **k: SimpleNamespace(open=lambda request, timeout=None: urlopen(request, timeout)))

    from services.capital.pg_store import CapitalPool
    cap_main.pool_store.create(CapitalPool(
        pool_id=POOL, name=POOL, owner_id=TENANT, owner_type="org", status="active",
        created_at="2026-11-22T00:00:00Z", currency="TWD", budget=100000.0, single_runtime_enforced=True,
        metadata={"tenant_id": TENANT, "persona_id": PERSONA, "execution_context": "paper",
                  "requested_risk_profile": "low", "require_approved_profile_mapping": True,
                  "risk_policy": dict(pool_policy)}))

    store = SimpleNamespace(get_by_persona=lambda *a, **k: None)
    monkeypatch.setattr(service, "_persona_provisioning_store", lambda: store)
    monkeypatch.setattr(service, "_runtime_bindings_for_case", lambda *a, **k: [])
    draft = DraftResult(True, policy=dict(DRAFT), provenance={"source": "risk_officer_draft", "agent_id": "risk-officer",
                                                              "input_digest": "e2e"})
    monkeypatch.setattr(PersonaProvisioningCoordinator, "_drafter", lambda self: (lambda inputs: draft))
    return SimpleNamespace(gov=gov, cap=cap, cap_main=cap_main, issued=issued, faults=faults, seen=seen)


def _repair(**kwargs):
    # The module-level function is exactly what the route calls.
    return service.repair_paper_pool_risk_policy(
        TENANT, PERSONA, capital_pool_id=POOL, requested_by="operator-1", requested_profile="low", **kwargs)


def _decisions(world):
    headers = {"Authorization": "Bearer " + world.issued["PANTHEON_PERSONA_GOVERNANCE_SERVICE_TOKEN"]}
    rows = world.gov.get("/api/governance/approvals", headers=headers, params={"tenant_id": TENANT})
    return rows.json() if rows.status_code == 200 else []


def _proposals(world):
    return [d for d in _decisions(world) if d.get("target_type") == "capital_pool_activation"]


def test_deployed_coordinator_carries_the_gate_identity():
    coordinator = service._chain_paper_capital_coordinator(TENANT)
    assert coordinator.governance_actor_id == GATE != coordinator.actor_id


def test_first_second_third_repair_end_to_end(world):
    first = _repair()
    req = first["owner_decision_request"]
    assert req["paper_gate_status"] == "decided_approved_by_automated_gate", req
    proposals = _proposals(world)
    assert len(proposals) == 1
    assert proposals[0]["decision_state"] == "decided" and proposals[0]["decision"] == "approved"
    assert any(v.get("actor_id") == GATE for v in proposals[0]["metadata"]["approvals"])
    second = _repair()
    assert second["repaired"] is True, second
    assert second["after_resolution"]["resolved"] is True
    assert second["after_resolution"]["allow_new_or_increased_risk"] is True
    assert second["after_resolution"]["profile_mapping_status"] != "unresolved_owner_decision_required"
    third = _repair()
    assert third["idempotent_noop"] is True and len(_proposals(world)) == 1


def test_direction_compares_the_draft_with_the_policy_in_effect_before(world):
    req = _repair()["owner_decision_request"]
    proposal = _proposals(world)[0]
    subject = proposal["metadata"]["subject"]
    assert subject["risk_direction"] == "reduce", subject
    assert subject["risk_comparison"]["gross_limit"] == "stricter"
    assert req["governance_proposal_id"] == proposal["decision_id"]


LOW_V1 = {**LEGACY_V1, "gross_limit": 0.1, "net_limit": 0.1, "max_leverage": 0.1}


@pytest.mark.parametrize("world", [LOW_V1], indirect=True)
def test_increase_direction_for_the_opposite_draft(world, monkeypatch):
    big = {**DRAFT, "gross_limit": 1.0, "net_limit": 1.0, "max_leverage": 1.0}
    monkeypatch.setattr(PersonaProvisioningCoordinator, "_drafter",
                        lambda self: (lambda inputs: DraftResult(True, policy=dict(big), provenance={
                            "source": "risk_officer_draft", "agent_id": "risk-officer", "input_digest": "e2e-inc"})))
    _repair()
    subject = _proposals(world)[0]["metadata"]["subject"]
    assert subject["risk_direction"] == "increase", subject


@pytest.mark.parametrize("suffix,status,detail,expected,http", [
    ("/review", 403, "Body actor and role must match verified principal", "gate_refused:403", 422),
    ("/decide", 403, "Body actor and role must match verified principal", "gate_refused:403", 422),
    ("/decide", 409, "version conflict", "gate_refused:409", 422),
])
def test_refused_gate_is_never_reported_as_approved(world, suffix, status, detail, expected, http):
    world.faults[suffix] = (status, detail)
    req = _repair()["owner_decision_request"]
    assert req["paper_gate_status"] == expected
    assert req["paper_gate_error"]["http_status"] == status and detail in req["paper_gate_error"]["reason"]
    assert not str(req["paper_gate_status"]).startswith("decided_")
    assert _proposals(world)[0]["decision_state"] != "decided"


def test_gate_timeout_is_unavailable(world):
    world.faults["/decide"] = TimeoutError("timed out")
    req = _repair()["owner_decision_request"]
    assert req["paper_gate_status"] == "gate_unavailable"
    assert _proposals(world)[0]["decision_state"] != "decided"


def test_pending_proposal_is_reused_and_decided_not_duplicated(world):
    world.faults["/review"] = (403, "forced")
    _repair()
    pending = _proposals(world)
    assert len(pending) == 1 and pending[0]["decision_state"] == "proposed"
    world.faults.clear()
    req = _repair()["owner_decision_request"]
    assert req["governance_proposal_status"] == "reused_existing_pending_proposal"
    assert req["paper_gate_status"] == "decided_approved_by_automated_gate"
    after = _proposals(world)
    assert len(after) == 1 and after[0]["decision_id"] == pending[0]["decision_id"]
    assert after[0]["decision"] == "approved"
