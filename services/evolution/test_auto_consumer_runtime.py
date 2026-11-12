"""Pool scope and durable removal/publication retries at the owner HTTP seams."""
import copy

import pytest

from services.evolution import auto_consumer_runtime as rt

TENANT = "tenant-a"
ENV = {"EVOLUTION_SCHEDULER_TENANT_ID": TENANT,
       "AUTO_CONSUMER_PERSONA_URL": "http://per", "AUTO_CONSUMER_PERSONA_TOKEN": "persona-test",
       "AUTO_CONSUMER_REGISTRY_URL": "http://reg", "AUTO_CONSUMER_REGISTRY_TOKEN": "registry-test",
       "AUTO_CONSUMER_BFF_URL": "http://bff", "AUTO_CONSUMER_BFF_TOKEN": "operator-test"}


@pytest.fixture
def owners():
    member = {"artifact_ref": "a", "checksum": "sha256:a", "joined_at": "2024-01-01T00:00:00Z",
              "stage": "active", "risk_weight": 1, "baseline_ref": "baseline-a"}
    pool = {"persona_id": "p", "tenant_id": TENANT, "pool_version": 1, "members": [member]}
    persona = {"persona_id": "p", "tenant_id": TENANT, "lifecycle_state": "paper_owner"}
    entry = {"registry_id": "a", "owner_tenant": TENANT, "version": "1.0.0", "artifact_state": "approved",
             "checksum": "sha256:a", "metadata": {"strategy_artifact": {"strategy_logic": {"kind": "factor_rule_v1"}}}}
    state = {"pool": pool, "persona": persona, "entry": entry, "publish_down": False, "writes": []}
    def transport(method, url, headers, body):
        assert headers["X-Tenant-Id"] == TENANT
        if url == "http://per/api/personas/p": return 200, persona
        if url == "http://per/api/personas/p/strategy-pool": return 200, state["pool"]
        if url == "http://reg/api/registry/strategy-artifacts/a": return 200, {"entry": entry}
        state["writes"].append((method, url, body))
        if url.endswith("/strategy-pool/versions"):
            if body["expected_version"] != state["pool"]["pool_version"]: return 409, {}
            state["pool"] = {**state["pool"], "pool_version": state["pool"]["pool_version"] + 1,
                             "members": copy.deepcopy(body["members"])}
            return 201, state["pool"]
        if url.endswith("/strategy-pool/publish"):
            return (503, {}) if state["publish_down"] else (200, {"data": {"status": "published"}})
        raise AssertionError("unexpected owner effect: " + url)
    decision = {"decision_id": "d", "tenant_id": TENANT, "target_id": "a", "target_version": "1.0.0",
                "persona_id": "p", "metadata": {"pool_member": {**member, "persona_id": "p"}}}
    return rt.PoolOwner(ENV, transport), decision, state


def test_scope_is_verified_pool_membership_without_allocator_bindings(owners):
    owner, decision, _ = owners
    assert owner.resolve(decision)["pool_member"]["artifact_ref"] == "a"
    assert not rt.enabled({})


@pytest.mark.parametrize("change", ["tenant", "lifecycle", "checksum", "version", "not_member", "not_approved"])
def test_changed_owner_facts_fail_closed(owners, change):
    owner, decision, state = owners
    if change == "tenant": state["persona"]["tenant_id"] = "other"
    if change == "lifecycle": state["persona"]["lifecycle_state"] = "live_owner"
    if change == "checksum": state["entry"]["checksum"] = "sha256:other"
    if change == "version": state["entry"]["version"] = "2.0.0"
    if change == "not_member": state["pool"]["members"] = []
    if change == "not_approved": state["entry"]["artifact_state"] = "candidate"
    assert owner.resolve(decision) is None
    assert not state["writes"]


def test_removal_retries_publication_without_repeating_pool_mutation(owners):
    owner, decision, state = owners
    scope = owner.resolve(decision)
    state["publish_down"] = True
    with pytest.raises(RuntimeError, match="503"):
        owner.retire(decision, scope)
    assert state["pool"]["members"][0]["stage"] == "removed"
    assert state["persona"]["lifecycle_state"] == "paper_owner"
    assert owner.resolve(decision) is not None  # only this decision can resume
    state["publish_down"] = False
    receipt = owner.retire(decision, owner.resolve(decision))
    assert receipt["already_removed"] is True and receipt["status"] == "published"
    assert len([call for call in state["writes"] if call[1].endswith("/versions")]) == 1
    assert not any("lifecycle" in call[1] for call in state["writes"])


def test_decision_reads_bind_the_configured_tenant_without_transport_help():
    seen = []

    def transport(method, url, headers, body):
        seen.append((method, url, headers.get("X-Tenant-Id")))
        return (200, []) if headers.get("X-Tenant-Id") else (400, {"detail": "X-Tenant-Id is required"})

    port = rt.HttpDecisionPort("http://evo", lambda: "tok", transport, TENANT)
    for state in ("proposed", "reviewed", "approved"):
        assert port.list_by_state(state) == []
    assert [t for _, _, t in seen] == [TENANT] * 3
    try:
        rt.HttpDecisionPort("http://evo", lambda: "tok", transport).list_by_state("proposed")
    except RuntimeError:
        pass
    else:
        raise AssertionError("an unbound tenant must fail closed")
