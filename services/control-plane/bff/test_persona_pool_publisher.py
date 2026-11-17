"""Unit tests for pool publish guards that need no owner transport."""
from __future__ import annotations

import pytest

from services.control_plane.bff.tests.test_persona_pool_publish_route import world  # noqa: F401  (fixture)

from services.control_plane.bff.persona_pool_publisher import (
    InMemoryPublishRecords,
    OwnerPoolReader,
    PersonaPoolPublisher,
    PoolPublishRefused,
)
from services.control_plane.persona.persona_strategy_pool import PoolNotFound


class _NoCoordinator:
    transport = None
    runtime_binding_reader = None

    def publish_pool_envelope(self, publication):  # pragma: no cover - must not run
        raise AssertionError("owner chain must not run")


class _Pool:
    def current(self, persona_id, tenant_id):
        raise PoolNotFound("no strategy pool for persona 'p'")

    version = lambda self, persona_id, tenant_id, number: self.current(persona_id, tenant_id)  # noqa: E731


def test_beyond_paper_refused_before_any_owner_read():
    publisher = PersonaPoolPublisher(coordinator=_NoCoordinator(), pool_store=_Pool())
    for stage in ("canary", "live", "none"):
        with pytest.raises(PoolPublishRefused) as exc:
            publisher.publish(tenant_id="t", persona_id="p", requested_by="op", stage=stage)
        assert exc.value.status_code == 403


def test_missing_pool_is_not_found_not_a_guess():
    publisher = PersonaPoolPublisher(coordinator=_NoCoordinator(), pool_store=_Pool())
    with pytest.raises(PoolPublishRefused) as exc:
        publisher.publish(tenant_id="t", persona_id="p", requested_by="op")
    assert exc.value.status_code == 404


def test_owner_pool_reader_maps_missing_to_not_found_and_reads_versions():
    seen = []

    def fetch(path, tenant):
        seen.append((path, tenant))
        return {"members": [], "pool_version": 3} if path.endswith("/versions/3") else None

    reader = OwnerPoolReader(fetch)
    assert reader.version("p", "t", 3)["pool_version"] == 3
    with pytest.raises(PoolNotFound):
        reader.current("p", "t")
    assert seen[0] == ("/api/personas/p/strategy-pool/versions/3", "t")


def test_publish_records_use_compare_and_set():
    records = InMemoryPublishRecords()
    ok, _ = records.compare_and_set("k", None, {"a": 1})
    stale, current = records.compare_and_set("k", None, {"a": 2})
    assert ok and not stale and current == {"a": 1}


def test_publisher_pins_screen_rules_without_enumerating_picks():
    from types import SimpleNamespace
    from services.registry.test_candidate_pool import screen_artifact
    from services.registry.strategy_artifact import strategy_artifact_checksum

    artifact = screen_artifact()
    checksum = strategy_artifact_checksum(artifact)
    entry = {"registry_id": artifact["artifact_id"], "version": artifact["version"],
             "checksum": checksum, "owner_tenant": "t", "artifact_state": "approved",
             "metadata": {"strategy_artifact": artifact}}
    coordinator = SimpleNamespace(transport=SimpleNamespace(get=lambda *_: entry))
    publisher = PersonaPoolPublisher(coordinator=coordinator, pool_store=_Pool())
    pool = {"members": [{"artifact_ref": artifact["artifact_id"], "checksum": checksum,
                          "stage": "active", "risk_weight": 1}]}
    result, pins = publisher._build_envelope("t", SimpleNamespace(strategy_id="pool"), pool,
        "envelope", "1.0.0", 1, "pool-digest", {"artifact_id": "previous"})
    assert result["parameters"]["symbols"] == []
    assert result["expression_policy"]["kind"] == "target_position_v1"
    assert pins[0]["checksum"] == checksum


def test_trial_publication_is_the_pool_policy_reading_not_a_blanket_zero():
    from services.control_plane.bff.persona_pool_publisher import trial_publication
    assert trial_publication({"stage": "trial", "risk_weight": 0}, .1) == (0, True)
    assert trial_publication({"stage": "trial", "risk_weight": .4}, .1) == (.1, False)
    assert trial_publication({"stage": "active", "risk_weight": .4}, .1) == (.4, False)


def test_default_publish_records_are_durable_files_not_process_memory(tmp_path):
    from services.control_plane.bff.persona_pool_publisher import build_publish_records
    env = {"PANTHEON_PERSONA_PROVISIONING_STORE_BACKEND": "memory",
           "PANTHEON_PERSONA_POOL_PUBLISH_STORE_PATH": str(tmp_path / "r.json")}
    first = build_publish_records(env)
    assert first.compare_and_set("t:p", None, {"published": {"1": {"status": "published"}}})[0]
    assert build_publish_records(env).get("t:p") == {"published": {"1": {"status": "published"}}}


# ---------------------------------------------------------------------------
# A pool with no live member keeps operating (POOL-EMPTY-POOL-OPERATION-20261116).
# Real: pool store + decide, baseline resolver on gate-minted proofs, admission replay on recorded TWSE
# bars, the publisher, coordinator and deployment dispatch. Registry reads are the World's owner boundary.
# ---------------------------------------------------------------------------
def _install_entry(world, entry):
    import copy
    world.entries[entry["registry_id"]] = entry
    world.transport.objects[("registry", f"/api/registry/strategy-artifacts/{entry['registry_id']}")] = {
        "entry": copy.deepcopy(entry), "deployment_stage": "none"}


def _buy_candidate(base):
    """A gate-minted entry whose strategy artifact is a distinct always-long variant of ``base``."""
    import copy
    from services.registry.strategy_artifact import strategy_artifact_checksum
    entry = copy.deepcopy(base)
    artifact = entry["metadata"]["strategy_artifact"]
    artifact["strategy_logic"].update(positive_action="BUY", non_positive_action="BUY")
    artifact["parameters"]["zero_momentum_action"] = "BUY"
    artifact.update(artifact_id="artifact-buy-variant", strategy_id="strategy-buy-variant")
    entry["checksum"] = strategy_artifact_checksum(artifact)
    entry["replication_proof"]["owner_verified_binding"]["artifact_checksum"] = entry["checksum"]
    entry.update(registry_id=artifact["artifact_id"], version=artifact["version"])
    return entry


def test_empty_pool_end_to_end_remove_publish_demand_admit_publish(world, monkeypatch, tmp_path):
    from services.control_plane.bff.tests.test_persona_pool_publish_route import (
        H, PATH, PERSONA, TENANT, _client, _genuine_trial_entry, _live)
    from services.control_plane.persona.persona_strategy_pool import PoolDecisionRequest, PoolMember
    from services.execution.lean_runtime.member_virtual_book import idle_pool_observations
    from services.persona.pool_autonomy_cycle import decide as cycle_decide
    from services.research.tests.test_pool_admission import recorded_dataset
    import pool_policy

    member_entry = _genuine_trial_entry()
    candidate_entry = _buy_candidate(member_entry)
    # The recorded momentum rule (the original member's own logic) does not clear admission on next-open fills.
    import copy
    from services.registry.strategy_artifact import strategy_artifact_checksum
    weak_entry = copy.deepcopy(member_entry)
    weak_entry["metadata"]["strategy_artifact"].update(artifact_id="artifact-weak", strategy_id="strategy-weak")
    weak_entry["checksum"] = strategy_artifact_checksum(weak_entry["metadata"]["strategy_artifact"])
    weak_entry["replication_proof"]["owner_verified_binding"]["artifact_checksum"] = weak_entry["checksum"]
    weak_entry.update(registry_id="artifact-weak", version=weak_entry["metadata"]["strategy_artifact"]["version"])
    for entry in (member_entry, candidate_entry, weak_entry):
        _install_entry(world, entry)
    client = _client(world, monkeypatch, tmp_path)
    ref, cand, weak = member_entry["registry_id"], candidate_entry["registry_id"], weak_entry["registry_id"]

    # 1. one live member, published
    live_member = PoolMember(artifact_ref=ref, checksum=member_entry["checksum"], stage="active", risk_weight=1.0,
                             baseline_ref=member_entry["approval_decision_id"], joined_at="2026-10-01T00:00:00Z")
    world.append([live_member], 0)
    assert client.post(PATH, json={}, headers=H).status_code == 200
    # 2. the only live member is removed; the empty version publishes through the existing path and keeps the runtime
    removed = live_member.model_copy(update={"stage": "removed", "risk_weight": 0,
        "removed_at": "2026-11-16T00:00:00Z", "removal_reason": "baseline_not_revalidated_next_open"})
    world.append([removed], 1)
    before = _live(world)[0]
    empty = client.post(PATH, json={}, headers=H)
    assert empty.status_code == 200 and empty.json()["data"]["status"] == "published", empty.text
    assert _live(world)[0]["runtime_id"] == before["runtime_id"]
    pool = world.pool.current(PERSONA, TENANT)
    assert pool["pool_version"] == 2 and all(m["stage"] == "removed" for m in pool["members"])
    # 3. idle observations are known and zero; health reports no live members, not an error
    window = pool_policy.load_policy().health_window_days
    days = [f"2026-10-{d:02d}" for d in range(1, 29)][-window:]
    observations = idle_pool_observations(days, source_ref="test:paper-runtime-risk-owner", gross_limit=100, drawdown_limit=.1)
    health = world.pool.health(PERSONA, TENANT, {}, observations)
    assert health["gross_utilisation"] == {"status": "known", "value": 0.0, "reason": None}
    assert health["drawdown_utilisation"]["value"] == 0.0 and health["idle_day_share"]["value"] == 1.0
    assert health["effective_bets"]["reason"] == "no_live_members"
    # 4. the daily cycle opens one demand, and not a second while it is open
    first = cycle_decide(pool=pool, health=health, open_demands=[], candidates=[], tried=[], now_day="2026-11-16")
    assert first["kind"] == "open_demand" and first["short_measure"] == "no_live_members"
    again = cycle_decide(pool=pool, health=health, open_demands=[{"status": "open", "short_measure": "no_live_members"}],
                         candidates=[], tried=[], now_day="2026-11-17")
    assert again["kind"] == "no_action"
    # 5. a candidate that does not clear the bar on its own is refused (nothing waived because the pool is idle)
    books = {cand: _book(candidate_entry["checksum"]), weak: _book(weak_entry["checksum"])}
    common = {"books": books, "dataset": recorded_dataset(), "observations": observations}
    refused = world.pool.decide(PERSONA, TENANT, PoolDecisionRequest(expected_version=2, execution_context="paper",
        action="admit", candidate={"artifact_ref": weak, "checksum": weak_entry["checksum"]}, **common), actor_id="op-1")
    assert refused["decision"]["status"] == "refused", refused["decision"]
    assert "candidate_oos_sharpe_below_persona_trial_threshold" in "; ".join(refused["decision"]["reasons"])
    assert refused["decision"]["backtest"]["pool_idle"] is True
    # 6. a gate-passing candidate inside the recorded universe is admitted as trial and published
    admitted = world.pool.decide(PERSONA, TENANT, PoolDecisionRequest(expected_version=3, execution_context="paper",
        action="admit", candidate={"artifact_ref": cand, "checksum": candidate_entry["checksum"]}, **common), actor_id="op-1")
    assert admitted["decision"]["status"] == "applied", admitted["decision"]["reasons"]
    trial = [m for m in admitted["members"] if m["stage"] != "removed"]
    assert len(trial) == 1 and trial[0]["artifact_ref"] == cand and trial[0]["stage"] == "trial"
    assert admitted["decision"]["approved_universe"] == ["2330"]
    published = client.post(PATH, json={}, headers=H)
    assert published.status_code == 200 and published.json()["data"]["pool_version"] == 4, published.text
    assert _live(world)[0]["artifact_id"] == published.json()["data"]["envelope_artifact_id"]


def _book(checksum):
    from datetime import date, timedelta
    return {"checksum": checksum, "source_ref": "test:virtual-book-owner", "since_inception": True,
            "basis": "underlying_exposure_fraction_net_taiwan_costs_equal_notional",
            "dates": [(date(2026, 10, 1) + timedelta(days=i)).isoformat() for i in range(30)],
            "returns": [.002, -.0005] * 15}
