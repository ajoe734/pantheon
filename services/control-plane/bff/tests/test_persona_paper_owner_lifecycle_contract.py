"""Fresh coordinator stimulus through actual PostgreSQL Governance/Registry owners.

Capital, Deployment and scheduling remain explicit isolated doubles. This proves
the caller/approval/artifact boundary, never a hosted RuntimeBinding or paper fill.
"""
from uuid import uuid4

import httpx

from services.control_plane.bff.persona_provisioning_coordinator import (
    PersonaProvisioningCoordinator, deterministic_provisioning_ids,
)
from services.control_plane.bff.personas.service import _PersonaOwnerHttpTransport
from services.control_plane.bff.test_persona_provisioning_coordinator import TrackingStore, _schedule_receipt
from services.control_plane.bff.tests.test_persona_governance_caller_contract import RealRegistryTransport
from services.governance.test_paper_approval_postgres import (  # noqa: F401 fixture imports
    PAPER_CLAIMS, _free_port, _server, headers, owner_env, paper_owners, token,
)


class RealApprovalRegistryTransport(RealRegistryTransport):
    def __init__(self, *args, governance_wire, **kwargs):
        super().__init__(*args, **kwargs)
        self.governance_wire = governance_wire

    def get(self, owner, path):
        if owner == "governance":
            return self.governance_wire.get(owner, path)
        return super().get(owner, path)

    def post(self, owner, path, payload):
        if owner == "governance":
            return self.governance_wire.post(owner, path, payload)
        return super().post(owner, path, payload)


def test_new_persona_crosses_real_approval_and_registry_and_reloads(paper_owners, monkeypatch):
    owners = paper_owners
    principal = "pantheon-dev-paper-provisioner"
    monkeypatch.setenv("PANTHEON_GOVERNANCE_APPROVAL_API_URL", owners["governance_url"])
    monkeypatch.setenv("PANTHEON_PERSONA_GOVERNANCE_ACTOR_ID", principal)
    monkeypatch.delenv("PANTHEON_PERSONA_GOVERNANCE_SERVICE_TOKEN_FILE", raising=False)
    monkeypatch.setenv("PANTHEON_PERSONA_GOVERNANCE_SERVICE_TOKEN", token(owners["secret"], **PAPER_CLAIMS))
    registry_token = token(owners["secret"], sub="control-plane-bff", roles=["operator"],
                           tenant="tenant-dev", tenant_id="tenant-dev", aud="isolated-registry")
    stimulus = uuid4().hex
    store = TrackingStore()
    record, created = store.reserve(
        tenant_id="tenant-dev", idempotency_key="isolated-fresh-" + stimulus,
        request_hash="isolated-request-" + stimulus, normalized_name="isolated-" + stimulus,
        persona_id="persona-isolated-" + stimulus,
        request_payload={"name": "Isolated fresh paper contract", "requested_by": "synthetic-operator",
                         "mandate": "Synthetic paper-only admission", "budget": 25000},
    )
    assert created
    ids = deterministic_provisioning_ids(record)
    with httpx.Client(base_url=owners["registry_url"], timeout=10) as registry:
        transport = RealApprovalRegistryTransport(
            registry, {"Authorization": "Bearer " + registry_token},
            governance_wire=_PersonaOwnerHttpTransport(tenant_id="tenant-dev"),
            governance_subject=principal,
        )
        coordinator = PersonaProvisioningCoordinator(
            store=store, transport=transport, schedule_registrar=_schedule_receipt,
            lease_owner="isolated-owner-lifecycle", actor_id="control-plane-bff",
            governance_actor_id=principal,
        )
        result = coordinator.coordinate(record)
        assert result.state == "provisioning", result.error
        registry_ids = [ids.baseline_registry_id, ids.baseline_strategy_artifact_id,
                        ids.registry_id, ids.strategy_artifact_id]
        captured = {}
        for registry_id in registry_ids:
            view = transport.get("registry", "/api/registry/entries/" + registry_id)
            entry = view["entry"]
            assert entry["artifact_state"] == "approved"
            assert entry["approver"] == principal
            assert entry["last_actor"]["actor_id"] == "control-plane-bff"
            evidence = entry["approval_evidence"]
            assert evidence["candidate_digest"] == entry["checksum"]
            assert evidence["persona_id"] == record.persona_id
            assert evidence["capital_pool_id"] == ids.capital_pool_id
            assert evidence["authorization_scope"]["allowed_target_stages"] == ["paper"]
            if entry["artifact_type"] == "execution_bundle":
                assert "binding_intent" not in entry["metadata"]["strategy_artifact"]
            captured[registry_id] = view

        # A second process uses only PostgreSQL authority, not the original process cache.
        with _server(owners["registry_env"], "services.registry.service:app", _free_port()) as restarted:
            for registry_id, expected in captured.items():
                response = httpx.get(restarted + "/api/registry/entries/" + registry_id,
                                    headers={"Authorization": "Bearer " + registry_token})
                assert response.status_code == 200 and response.json() == expected
        with _server(owners["governance_env"], "services.governance.main:app", _free_port()) as restarted:
            for view in captured.values():
                approval_id = view["entry"]["approval_decision_id"]
                response = httpx.get(restarted + "/api/governance/approvals/" + approval_id,
                                    headers=headers(owners["governance_env"], **PAPER_CLAIMS))
                assert response.status_code == 200
                assert response.json()["authorization_scope"] == view["entry"]["approval_evidence"]["authorization_scope"]
                assert response.json()["candidate_digest"] == view["entry"]["checksum"]
