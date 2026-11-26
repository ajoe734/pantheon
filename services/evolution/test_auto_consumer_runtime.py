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


def test_books_are_keyed_by_pool_registry_ref_instead_of_embedded_artifact_id():
    env = {**ENV, 'AUTO_CONSUMER_CAPITAL_URL': 'http://cap', 'AUTO_CONSUMER_CAPITAL_TOKEN': 'capital-test',
           'AUTO_CONSUMER_TELEMETRY_URL': 'http://tel', 'AUTO_CONSUMER_TELEMETRY_TOKEN': 'telemetry-test'}
    def transport(method, url, headers, body):
        if url.startswith('http://cap'):
            return 200, [{'binding_id': 'pcb', 'persona_id': 'p', 'tenant_id': TENANT, 'status': 'active'}]
        return 200, {'summaries': [{'tenant_id': TENANT, 'deployment_stage': 'paper',
            'persona_capital_binding_id': 'pcb', 'member_virtual_books': [
                {'member_id': 'registry-alias', 'artifact_id': 'embedded-a', 'checksum': 'sha256:a'}]}]}
    assert list(rt.PoolOwner(env, transport).books({'persona_id': 'p'})) == ['registry-alias']


def test_requalification_removal_records_verdict_and_learns_family_in_persona_memory(owners):
    owner, decision, state = owners
    verdict = {"verdict_ref": "gate:g-1:bt-1", "passed": False, "failed_checks": ["oos_sharpe_positive_after_costs"]}
    receipt = owner.remove_member(
        state["pool"],
        state["pool"]["members"][0],
        reason="requalification_failed_next_open_fill",
        verdict_ref="gate:g-1:bt-1",
        verdict=verdict,
    )
    assert receipt["status"] == "published" and receipt["verdict_ref"] == "gate:g-1:bt-1"
    removed = state["pool"]["members"][0]
    assert removed["stage"] == "removed"
    assert removed["removal_reason"] == "requalification_failed_next_open_fill"
    assert removed["removal_verdict_ref"] == "gate:g-1:bt-1"
    version_write = next(body for _, url, body in state["writes"] if url.endswith("/strategy-pool/versions"))
    assert version_write["change_reason"] == "requalification_failed_next_open_fill:gate:g-1:bt-1"
    assert version_write["decision"]["verdict_ref"] == "gate:g-1:bt-1"
    assert version_write["decision"]["verdict"] == verdict
    # PoolOwner.resolve still resolves a member removed by requalification so retries can confirm publication.
    assert owner.resolve(decision) is not None

    mem_calls = []
    env = {**ENV, "AUTO_CONSUMER_MEMORY_URL": "http://mem", "AUTO_CONSUMER_MEMORY_TOKEN": "mem-tok"}

    def mem_transport(method, url, headers, body):
        mem_calls.append((method, url, body))
        return 200, {"status": "written", "memory_id": "m-1"}

    mem_owner = rt.PoolOwner(env, mem_transport)
    mem_receipt = mem_owner.learn_member_removal(
        persona_id="p",
        artifact_ref="a",
        decision_id="d",
        verdict_ref="gate:g-1:bt-1",
        families=["strat-tw-replication-001"],
    )
    assert mem_receipt["status"] == "written"
    assert len(mem_calls) == 1
    _, mem_url, mem_body = mem_calls[0]
    assert mem_url == "http://mem/api/memory/writebacks/learn-feedback"
    assert "tried_family:strat-tw-replication-001" in mem_body["tags"]
    assert "requalification_failed_next_open_fill" in mem_body["tags"]



# REQUALIFY-REFUSAL-NOT-CRASH-20261125: a stated pool refusal of a requalified replacement defers
# to the operator (SD_PERSONA_STRATEGY_POOL_AUTONOMY 5.2); a malformed decision keeps the RuntimeError.
# The replay-book builders are owner-side and proven by the integration test; here they are fixed so the
# test isolates how replace_requalified_member treats the pool owner's decision row.
def _replacement_owner(monkeypatch, owners, decision_row):
    import services.execution.lean_runtime.member_virtual_book as mvb
    import services.persona.pool_autonomy_cycle as cycle

    owner, _, state = owners
    monkeypatch.setattr(cycle, "candidate_virtual_book", lambda **kw: {
        "checksum": kw["checksum"], "source_ref": "governed-replay:recorded", "dates": ["2026-10-29", "2026-10-30"],
        "returns": [0.0, 0.0]})
    monkeypatch.setattr(mvb, "idle_pool_observations", lambda dates, **kw: [{"date": d} for d in dates])
    inner = owner.send

    def transport(method, url, headers, body):
        if url.endswith("/strategy-pool/decisions"):
            state["writes"].append((method, url, body))
            # The pool owner records the decision as a version with unchanged membership.
            return 201, {**state["pool"], "pool_version": state["pool"]["pool_version"] + 1, "decision": decision_row}
        return inner(method, url, headers, body)

    owner.send = transport
    ctx = {"persona_id": "p", "artifact_ref": "a", "checksum": "sha256:a", "joined_at": "2024-01-01T00:00:00Z"}
    return owner, state, ctx


REFUSED_ROW = {"action": "admit", "status": "refused", "persona_trial_count": 1, "execution_context": "paper",
               "reasons": ["governed_market_boundary_unavailable: governed daily-bar store is stale"]}


def test_refused_requalified_replacement_is_deferred_not_crashed(monkeypatch, owners):
    from services.research.alpha_replication.evolution_executor import RequalifyDeferred

    owner, state, ctx = _replacement_owner(monkeypatch, owners, REFUSED_ROW)
    with pytest.raises(RequalifyDeferred) as info:
        owner.replace_requalified_member(ctx, new_artifact={"artifact_id": "a-rq", "strategy_id": "s"},
                                         new_checksum="sha256:rq", dataset={"records": []})
    deferred = info.value
    assert deferred.reason == "pool_admission_refused"
    assert deferred.detail["pool_decision_reasons"] == REFUSED_ROW["reasons"]
    assert deferred.detail["candidate_artifact_ref"] == "a-rq" and deferred.detail["live_member_ref"] == "a"
    assert deferred.detail["pool_version_before_decision"] == 1
    # No pool version written by this path and nothing published; the live member is untouched.
    assert [url for _, url, _ in state["writes"]] == ["http://per/api/personas/p/strategy-pool/decisions"]
    assert state["pool"]["members"][0]["stage"] == "active" and state["pool"]["pool_version"] == 1


@pytest.mark.parametrize("row", [None, {"status": "pending"}, {"status": "refused", "reasons": []},
                                 {"status": "refused", "reasons": "not-a-list"}])
def test_malformed_replacement_decision_keeps_runtime_error(monkeypatch, owners, row):
    from services.research.alpha_replication.evolution_executor import RequalifyDeferred

    owner, state, ctx = _replacement_owner(monkeypatch, owners, row)
    with pytest.raises(RuntimeError, match="requalified replacement admission refused") as info:
        owner.replace_requalified_member(ctx, new_artifact={"artifact_id": "a-rq", "strategy_id": "s"},
                                         new_checksum="sha256:rq", dataset={"records": []})
    assert not isinstance(info.value, RequalifyDeferred)
    assert not any(url.endswith("/publish") for _, url, _ in state["writes"])
