"""Coordinator payloads through authoritative Registry admission, no fake schema.

These are isolated in-memory owner admissions, not hosted writes or research.
"""
from dataclasses import replace

import pytest

from services.control_plane.bff.test_persona_provisioning_coordinator import (
    FakeOwnerTransport, _record_and_store,
)
from services.control_plane.bff.persona_provisioning_coordinator import (
    PersonaProvisioningCoordinator, deterministic_provisioning_ids,
)
from services.registry.service import StrategySpecRegisterRequest, _strategy_spec_register_payload
from services.registry.models import RegistryEntry
from services.registry.strategy_artifact import build_strategy_artifact_registry_payload


@pytest.mark.parametrize("baseline", [True, False])
def test_coordinator_spec_and_bundle_pass_real_registry_and_paper_admission(baseline):
    from services.governance.paper_approval_scope import (
        PaperCandidateExpectation, verify_paper_registry_candidate,
    )

    store, original = _record_and_store()
    record = replace(original, tenant_id="tenant-dev")
    ids = deterministic_provisioning_ids(record)
    coordinator = PersonaProvisioningCoordinator(
        store=store, transport=FakeOwnerTransport(), schedule_registrar=lambda *args: {},
        lease_owner="isolated-registry-contract", governance_actor_id="pantheon-dev-paper-provisioner",
    )
    spec_payload = coordinator._strategy_spec_payload(record, ids, baseline=baseline)
    admitted = _strategy_spec_register_payload(StrategySpecRegisterRequest.model_validate(spec_payload))
    spec_entry = RegistryEntry(registry_id=spec_payload["registry_id"],
                               owner_tenant="tenant-dev", **vars(admitted)).to_dict()
    spec_expectation = PaperCandidateExpectation(
        tenant_id="tenant-dev", persona_id=record.persona_id, capital_pool_id=ids.capital_pool_id,
        target_id=spec_entry["registry_id"], target_version=spec_entry["version"],
        candidate_digest=spec_entry["checksum"],
    )
    verify_paper_registry_candidate({"entry": spec_entry}, expectation=spec_expectation,
                                    read_entry_view=lambda _: pytest.fail("spec has no parent read here"))

    bundle_payload = coordinator._strategy_artifact_payload(record, ids, baseline=baseline)
    bundle_id, bundle_admitted = build_strategy_artifact_registry_payload(bundle_payload)
    bundle_entry = RegistryEntry(registry_id=bundle_id, owner_tenant="tenant-dev",
                                 **vars(bundle_admitted)).to_dict()
    spec_entry["artifact_state"] = "approved"

    def read_spec(identity):
        assert identity == spec_entry["registry_id"]
        return {"entry": spec_entry}

    bundle_expectation = PaperCandidateExpectation(
        tenant_id="tenant-dev", persona_id=record.persona_id, capital_pool_id=ids.capital_pool_id,
        target_id=bundle_id, target_version=bundle_entry["version"],
        candidate_digest=bundle_entry["checksum"],
    )
    verify_paper_registry_candidate({"entry": bundle_entry}, expectation=bundle_expectation,
                                    read_entry_view=read_spec)
