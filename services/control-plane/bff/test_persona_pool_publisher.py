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


def _dispatched_world(world):
    from services.control_plane.bff.tests.test_persona_pool_publish_route import PERSONA, TENANT
    world.append([world.member("artifact-a", 1.0)], 0)
    world.publisher.publish(tenant_id=TENANT, persona_id=PERSONA, requested_by="test")
    history = world.publisher.history(TENANT, PERSONA)
    # The hosted legacy shape: binding moved, receipt remains dispatched, no runtime_id.
    history["published"]["1"]["status"] = "dispatched"
    history["current_envelope"] = None
    key = world.publisher._key(TENANT, PERSONA)
    old = world.publisher.records.get(key)
    assert world.publisher.records.compare_and_set(key, old, history)[0]
    return TENANT, PERSONA, history


def test_hosted_dispatch_v2_to_v6_keeps_one_binding_read_per_cycle(world, monkeypatch, tmp_path):
    from services.control_plane.bff.tests.test_persona_pool_publish_route import H, PERSONA, TENANT, _client
    from services.persona.pool_autonomy_cycle import HttpPoolCyclePorts, PoolAutonomyCycle
    from services.persona.pool_publish_port import membership_digest

    for expected in range(2):
        world.append([world.member("artifact-a", 1.0)], expected)
    world.publisher.publish(tenant_id=TENANT, persona_id=PERSONA, requested_by="test", pool_version=2)
    key = world.publisher._key(TENANT, PERSONA)
    old = world.publisher.records.get(key)
    import copy
    legacy = copy.deepcopy(old)
    legacy["published"]["2"]["status"] = "dispatched"
    for pin in legacy["published"]["2"]["members"]:
        pin.pop("joined_at", None)
    legacy["current_envelope"] = None
    assert world.publisher.records.compare_and_set(key, old, legacy)[0]
    for expected in range(2, 6):
        world.append([world.member("artifact-a", 1.0)], expected)
    current = world.pool.current(PERSONA, TENANT)
    pending = {"decision_id": "old-publication-decision", "created_by_id": "persona-svc", "tenant_id": TENANT,
               "persona_id": PERSONA, "decision_state": "proposed", "metadata": {
                   "decision_kind": "pool_envelope_publication", "paper_only": True,
                   "membership_digest": membership_digest(current)}}
    retired = []
    client = _client(world, monkeypatch, tmp_path)
    def transport(method, service, path, tenant, body=None):
        if service == "evolution":
            if method == "GET":
                return [pending] if pending["decision_state"] == "proposed" else []
            assert path.endswith("/supersede")
            pending["decision_state"] = "superseded"
            retired.append(body)
            return pending
        response = client.request(method, path, json=body, headers=H)
        assert response.status_code == 200, response.text
        return response.json()
    reads = []
    reader = world.coordinator.runtime_binding_reader
    world.coordinator.runtime_binding_reader = lambda *args: reads.append(args) or reader(*args)
    ports = HttpPoolCyclePorts(transport, pool_store=world.pool,
                              consultation_factory=lambda *a: pytest.fail("no consultation"))
    cycle = PoolAutonomyCycle(ports)
    persona = {"persona_id": PERSONA, "tenant_id": TENANT}
    first = cycle._publish(persona, "confirm-v2")
    assert first["status"] == "published" and first["pool_version"] == 2 and first["next_pool_version"] == 6
    assert len(reads) == 1 and len(retired) == 1 and pending["decision_state"] == "superseded"
    for packet, expected_status, expected_reads in (("dispatch-v6", "unconfirmed", 1),
                                                   ("confirm-v6", "unchanged", 1), ("settled", "unchanged", 0)):
        reads.clear()
        result = cycle._publish(persona, packet)
        assert result["status"] == expected_status, result
        assert len(reads) == expected_reads
    history = world.publisher.history(TENANT, PERSONA)
    assert history["current_envelope"]["pool_version"] == 6
    assert history["published"]["6"]["members"][0]["joined_at"] == current["members"][0]["joined_at"]
    assert len(retired) == 1 and len(world.pool.versions(PERSONA, TENANT)) == 6


def test_dispatched_running_envelope_confirms_once_without_registry_or_dispatch(world):
    tenant, persona, before = _dispatched_world(world)
    reads = []
    reader = world.coordinator.runtime_binding_reader
    world.coordinator.runtime_binding_reader = lambda *a: reads.append(a) or reader(*a)
    world.coordinator.publish_pool_envelope = lambda *a: pytest.fail("must not redispatch")
    world.publisher._registry_view = lambda *a: pytest.fail("no registry read needed to confirm")
    out = world.publisher.publish(tenant_id=tenant, persona_id=persona, requested_by="test", pool_version=1)
    history = world.publisher.history(tenant, persona)
    assert out["status"] == "published" and len(reads) == 1
    assert history["current_envelope"]["artifact_id"] == before["published"]["1"]["envelope_artifact_id"]
    assert history["published"]["1"] == {**before["published"]["1"], "status": "published"}
    assert history["refusals"] == before["refusals"]


def test_unmoved_dispatch_retries_existing_owner_chain_with_one_snapshot(world):
    from services.control_plane.bff.tests.test_persona_pool_publish_route import PERSONA, TENANT
    world.append([world.member("artifact-a", 1.0)], 0)
    world.transport.queue_dispatch = True
    reads = []
    reader = world.coordinator.runtime_binding_reader
    world.coordinator.runtime_binding_reader = lambda *a: reads.append(a) or reader(*a)
    for _ in range(2):
        reads.clear()
        out = world.publisher.publish(tenant_id=TENANT, persona_id=PERSONA, requested_by="cycle",
                                      pool_version=1, defer_confirmation=True)
        assert out["status"] == "dispatched" and len(reads) == 1
    assert world.publisher.history(TENANT, PERSONA)["current_envelope"] is None


def test_pending_owner_read_failure_remains_dispatched_without_new_refusal(world):
    tenant, persona, before = _dispatched_world(world)
    def unavailable(*a):
        raise RuntimeError("owner_unavailable")
    world.coordinator.runtime_binding_reader = unavailable
    out = world.publisher.publish(tenant_id=tenant, persona_id=persona, requested_by="cycle",
                                  pool_version=1, defer_confirmation=True)
    assert out["status"] == "dispatched" and "owner_unavailable" in out["note"]
    assert world.publisher.history(tenant, persona) == before


@pytest.mark.parametrize("field,value", [("artifact_id", "other"), ("artifact_version", "9.9.9"),
    ("deployment_mode", "live"), ("persona_capital_binding_id", "foreign"), ("status", "paused")])
def test_confirmation_requires_exact_active_paper_binding(world, field, value):
    from types import SimpleNamespace
    tenant, persona, history = _dispatched_world(world)
    entry = history["published"]["1"]
    binding = next(b for b in world.runtime.bindings.values() if b["status"] == "active")
    ids = SimpleNamespace(capital_pool_id=binding["capital_pool_id"],
                          persona_capital_binding_id=binding["persona_capital_binding_id"])
    assert not world.publisher._binding_moved(ids, entry, bindings=[{**binding, field: value}])
    assert world.publisher.history(tenant, persona)["published"]["1"]["status"] == "dispatched"


def test_confirmation_does_not_choose_between_two_nonterminal_bindings(world):
    from types import SimpleNamespace
    tenant, persona, history = _dispatched_world(world)
    binding = next(b for b in world.runtime.bindings.values() if b["status"] == "active")
    ids = SimpleNamespace(capital_pool_id=binding["capital_pool_id"],
                          persona_capital_binding_id=binding["persona_capital_binding_id"])
    assert not world.publisher._binding_moved(ids, history["published"]["1"],
                                              bindings=[binding, {**binding, "status": "paused"}])


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
    # POOL-ADMISSION-GOVERNED-UNIVERSE-20261125: paper admission reads the governed boundary (row shape of MarketBoundary)
    from types import SimpleNamespace
    world.pool._market_boundary = lambda: SimpleNamespace(
        snapshot_id="gmd-2026-11-24-0123456789abcdef", as_of="2026-11-25T00:00:00+00:00",
        symbols=("2330.TW", "2317.TW", "0050.TW"))
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
    # 6. a gate-passing candidate inside the governed boundary is admitted as trial and published
    admitted = world.pool.decide(PERSONA, TENANT, PoolDecisionRequest(expected_version=3, execution_context="paper",
        action="admit", candidate={"artifact_ref": cand, "checksum": candidate_entry["checksum"]}, **common), actor_id="op-1")
    assert admitted["decision"]["status"] == "applied", admitted["decision"]["reasons"]
    trial = [m for m in admitted["members"] if m["stage"] != "removed"]
    assert len(trial) == 1 and trial[0]["artifact_ref"] == cand and trial[0]["stage"] == "trial"
    rule = admitted["decision"]["universe_rule"]  # replaces the empty-pool stopgap's recorded symbol list
    assert rule["previous_member_symbols"] == ["2330"] and rule["boundary_symbol_count"] == 3
    assert rule["boundary_snapshot_id"] == "gmd-2026-11-24-0123456789abcdef" and "approved_universe" not in admitted["decision"]
    published = client.post(PATH, json={}, headers=H)
    assert published.status_code == 200 and published.json()["data"]["pool_version"] == 4, published.text
    assert _live(world)[0]["artifact_id"] == published.json()["data"]["envelope_artifact_id"]


def _book(checksum):
    from datetime import date, timedelta
    return {"checksum": checksum, "source_ref": "test:virtual-book-owner", "since_inception": True,
            "basis": "underlying_exposure_fraction_net_taiwan_costs_equal_notional",
            "dates": [(date(2026, 10, 1) + timedelta(days=i)).isoformat() for i in range(30)],
            "returns": [.002, -.0005] * 15}


def _screened_candidate(tenant):
    """A gate-minted entry (recorded 2330+2317 run) whose embedded artifact is a factor_rule screen.

    Validated universe 2330.TW + 2317.TW: wider than the live member's 2330, inside the governed boundary.
    """
    from services.evolution.test_approved_baselines import genuine_entry
    from services.registry.strategy_artifact import strategy_artifact_checksum
    from services.registry.test_candidate_pool import screen_artifact
    entry = genuine_entry("recorded_backtest_tw_2330_2317", ("2330.TW", "2317.TW"), tenant=tenant)
    artifact = screen_artifact()
    artifact.update(artifact_id="artifact-screen", strategy_id="strategy-screen")
    artifact["parameters"]["data_source"] = entry["metadata"]["strategy_artifact"]["parameters"]["data_source"]
    # Hold the least-traded name of the governed market each day (clears the unchanged admission bar on the recorded bars).
    artifact["strategy_logic"]["screen"]["steps"][1]["side"] = "bottom"
    entry["metadata"]["strategy_artifact"] = artifact
    entry["checksum"] = strategy_artifact_checksum(artifact)
    entry["replication_proof"]["owner_verified_binding"]["artifact_checksum"] = entry["checksum"]
    entry.update(registry_id=artifact["artifact_id"], version=artifact["version"])
    return entry


def _recorded_whole_market_dataset():
    """The recorded TWSE 2330/2317 admission bars plus the candidate_pool_market.v1 view of the same official rows."""
    import json
    from services.research.tests.test_pool_admission import FIXTURE, recorded_dataset
    dataset = recorded_dataset()
    traded = {}
    for item in json.loads(FIXTURE.read_text())["responses"]:
        for row in item["response"]["data"]:
            year, month, day = map(int, row[0].split("/"))
            traded[(item["symbol"], f"{year + 1911:04}-{month:02}-{day:02}")] = float(row[2].replace(",", ""))
    symbols = {}
    for record in dataset["records"]:
        code = record["instrument"].split(".")[0]
        symbols.setdefault(f"{code}.TW", {"dataset": "tw_price_daily", "bars": [], "features": {}})["bars"].append({
            "event_time": record["date"] + "T13:30:00+08:00", "available_time": record["date"] + "T18:00:00+08:00",
            **{k: record[k] for k in ("open", "high", "low", "close", "volume")},
            "traded_value": traded[(code, record["date"])], "suspended": False})
    sessions = sorted({r["date"] for r in dataset["records"]})
    dataset["candidate_pool_market"] = {"schema_version": "candidate_pool_market.v1", "scope": "all_governed_tw",
        "source_refs": dataset["source_dataset_refs"], "includes_delisted": False, "includes_suspended": False,
        "symbols": symbols, "trading_sessions": sessions, "calendar_coverage_through": sessions[-1]}
    return dataset


def test_screened_member_admitted_by_governed_boundary_is_enveloped_and_published(world, monkeypatch, tmp_path):
    """AC5 (POOL-ADMISSION-GOVERNED-UNIVERSE-20261125): real PersonaStrategyPoolStore.decide paper admit of a
    screened member whose validated universe is wider than the pool's, then the real publish route."""
    import json
    from types import SimpleNamespace
    from services.control_plane.bff.tests.test_persona_pool_publish_route import (
        H, PATH, PERSONA, TENANT, _client, _envelope_members, _genuine_trial_entry, _live)
    from services.control_plane.persona.persona_strategy_pool import PoolDecisionRequest, PoolMember
    from services.execution.lean_runtime.member_virtual_book import idle_pool_observations
    import pool_policy

    member_entry, screened = _genuine_trial_entry(), _screened_candidate(TENANT)
    for entry in (member_entry, screened):
        _install_entry(world, entry)
    client = _client(world, monkeypatch, tmp_path)
    boundary_only = tuple(f"{9000 + i}.TW" for i in range(40))  # governed symbols no member or candidate trades
    world.pool._market_boundary = lambda: SimpleNamespace(snapshot_id="gmd-2026-11-24-screen",
        as_of="2026-11-25T00:00:00+00:00", symbols=("2330.TW", "2317.TW") + boundary_only)
    ref, cand = member_entry["registry_id"], screened["registry_id"]
    world.append([PoolMember(artifact_ref=ref, checksum=member_entry["checksum"], stage="active", risk_weight=.5,
        baseline_ref=member_entry["approval_decision_id"], joined_at="2026-10-01T00:00:00Z")], 0)
    assert client.post(PATH, json={}, headers=H).status_code == 200
    calls_before, runtime_before = list(world.runtime.calls), _live(world)[0]
    positions, ledger = dict(world.runtime.positions), {k: list(v) for k, v in world.runtime.ledger.items()}

    window = pool_policy.load_policy().health_window_days
    days = [f"2026-10-{d:02d}" for d in range(1, 29)][-window:]
    observations = idle_pool_observations(days, source_ref="test:paper-runtime-risk-owner", gross_limit=100, drawdown_limit=.1)
    admitted = world.pool.decide(PERSONA, TENANT, PoolDecisionRequest(expected_version=1, execution_context="paper",
        action="admit", candidate={"artifact_ref": cand, "checksum": screened["checksum"]},
        books={ref: _book(member_entry["checksum"]), cand: _book(screened["checksum"])},
        dataset=_recorded_whole_market_dataset(), observations=observations), actor_id="op-1")
    decision = admitted["decision"]
    assert decision["status"] == "applied", decision["reasons"]
    # Admitted through the boundary rule: the old members-only rule would have refused 2317.
    rule = decision["universe_rule"]
    assert rule["candidate_symbols"] == ["2317", "2330"] and rule["previous_member_symbols"] == ["2330"]
    assert rule["boundary_snapshot_id"] == "gmd-2026-11-24-screen" and rule["boundary_symbol_count"] == 42
    assert "approved_universe" not in decision and decision["backtest"]["admitted"] is True
    assert {m["artifact_ref"]: m["stage"] for m in admitted["members"]} == {ref: "active", cand: "trial"}

    published = client.post(PATH, json={}, headers=H)
    assert published.status_code == 200, published.text
    data = published.json()["data"]
    assert data["status"] == "published" and data["pool_version"] == 2
    envelope = world.transport.objects[
        ("registry", f"/api/registry/strategy-artifacts/{data['envelope_artifact_id']}")]["entry"]["metadata"]["strategy_artifact"]
    pins = _envelope_members(world, data)
    assert set(pins) == {ref, cand} and pins[cand]["checksum"] == screened["checksum"]
    # The screen rules are pinned as the member's own artifact; no pick list and no boundary copy.
    assert pins[cand]["artifact"]["strategy_logic"]["screen"] == screened["metadata"]["strategy_artifact"]["strategy_logic"]["screen"]
    # Envelope symbols are the non-screened member's own (2330); the screen enumerates no picks (2317 absent).
    assert envelope["parameters"]["symbols"] == member_entry["metadata"]["strategy_artifact"]["parameters"]["symbols"]
    def strings(node):
        if isinstance(node, dict):
            return {x for k, v in node.items() for x in strings(k) | strings(v)}
        return {x for v in node for x in strings(v)} if isinstance(node, list) else {str(node)}
    copied = {s for s in boundary_only for s in (s, s.split(".")[0])} & (strings(envelope) | strings(admitted))
    assert not copied and len(json.dumps(rule)) < 1000  # the boundary list is neither in the envelope nor the version
    # Paper only: the existing in-stage replace of the same runtime; no new deploy, order, capital or live path.
    live = _live(world)
    assert world.runtime.calls == calls_before + ["replace"] and len(live) == 1
    assert live[0]["runtime_id"] == runtime_before["runtime_id"] and live[0]["deployment_mode"] == "paper"
    assert live[0]["capital_pool_id"] == runtime_before["capital_pool_id"]
    assert world.runtime.positions == positions and world.runtime.ledger == ledger


# ---------------------------------------------------------------------------
# POOL-MEMBER-JOINED-AT-CARRY-20261126: an envelope running without joined_at gets it through one
# automatic republish. Real: pool store, cycle publish step, cycle HTTP ports, BFF publish route,
# publisher, coordinator, governance and deployment fakes of the World, the producer and the book.
# ---------------------------------------------------------------------------
def test_running_envelope_without_joined_at_is_republished_once_and_reaches_the_book(world, monkeypatch, tmp_path):
    from services.control_plane.bff import persona_pool_publisher as publisher_module
    from services.control_plane.bff.tests.test_persona_pool_publish_route import H, PERSONA, TENANT, _client, _live
    from services.control_plane.persona.test_pool_policy import _producer_books
    from services.persona.pool_autonomy_cycle import HttpPoolCyclePorts, PoolAutonomyCycle

    client = _client(world, monkeypatch, tmp_path)
    world.append([world.member("artifact-a", 1.0)], 0)
    joined = world.pool.current(PERSONA, TENANT)["members"][0]["joined_at"]

    # 1. The envelope running on hosted today: published before the carry (no joined_at in envelope or pins).
    carry = publisher_module.PersonaPoolPublisher._build_envelope

    def before_the_carry(self, tenant_id, ids, pool, *rest):
        # The dev publisher before 6cc27c784 wrote neither envelope members nor pins with joined_at.
        bare = {**pool, "members": [{k: v for k, v in m.items() if k != "joined_at"} for m in pool["members"]]}
        envelope, pins = carry(self, tenant_id, ids, bare, *rest)
        return envelope, [{k: v for k, v in pin.items() if k != "joined_at"} for pin in pins]

    monkeypatch.setattr(publisher_module.PersonaPoolPublisher, "_build_envelope", before_the_carry)
    assert client.post(f"/bff/personas/{PERSONA}/strategy-pool/publish", json={}, headers=H).status_code == 200
    monkeypatch.setattr(publisher_module.PersonaPoolPublisher, "_build_envelope", carry)
    old = _live(world)[0]
    assert "joined_at" not in _envelope_body(world, old["artifact_id"])["strategy_logic"]["members"][0]

    # 2. The cycle's publish step through the real HTTP ports (BFF calls go to the real route).
    def transport(method, service, path, tenant, body=None):
        if service == "evolution" and method == "GET":
            return []
        assert service == "bff" and tenant == TENANT
        response = client.request(method, path, json=body, headers=H)
        assert response.status_code == 200, response.text
        return response.json()

    persona = {"persona_id": PERSONA, "tenant_id": TENANT}
    ports = HttpPoolCyclePorts(transport, pool_store=world.pool, consultation_factory=lambda *a: pytest.fail("no consultation"))
    cycle = PoolAutonomyCycle(ports)
    first = cycle._publish(persona, "pk-1")
    assert first["status"] == "unconfirmed", first
    assert first["reasons"] == ["envelope_member_admission_missing:artifact-a"] and first["republished_version"] == 2
    versions = world.pool.versions(PERSONA, TENANT)
    assert len(versions) == 2 and versions[1]["members"] == versions[0]["members"]
    assert versions[1]["decision"]["action"] == "envelope_republish"

    # 3. The new running envelope carries joined_at; same runtime, paper, automated governance approval.
    new = _live(world)[0]
    assert new["runtime_id"] == old["runtime_id"] and new["artifact_id"] == first["envelope_artifact_id"]
    envelope = _envelope_body(world, new["artifact_id"])
    [pinned] = envelope["strategy_logic"]["members"]
    assert pinned["joined_at"] == joined and pinned["risk_weight"] == 1.0
    approval = world.transport.objects[("governance", f"/api/governance/approvals/{_approval_id(client, 2)}")]
    assert approval["decision"] == "approved" and approval["actor_role"] == "automated_gate"

    # 4. The producer's member views carry it into the book's admitted_at.
    _, [book] = _producer_books(envelope, first_day=joined[:10], sessions=3)
    assert book["admitted_at"] == joined

    # 5. Once per persona: the next cycle publishes nothing and appends nothing.
    second = cycle._publish(persona, "pk-2")
    assert second["status"] == "unchanged" and second["reasons"] == []
    assert len(world.pool.versions(PERSONA, TENANT)) == 2 and _live(world)[0]["artifact_id"] == new["artifact_id"]


def _envelope_body(world, artifact_id):
    return world.transport.objects[("registry", f"/api/registry/strategy-artifacts/{artifact_id}")]["entry"]["metadata"]["strategy_artifact"]


def _approval_id(client, pool_version):
    from services.control_plane.bff.tests.test_persona_pool_publish_route import H, PERSONA
    history = client.get(f"/bff/personas/{PERSONA}/strategy-pool/publish-history", headers=H).json()["data"]
    return history["published"][str(pool_version)]["approval_decision_id"]
