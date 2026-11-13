"""Policy and isolation of the persona's daily pool cycle (fake ports; no network)."""
from __future__ import annotations

import pytest

from services.persona.pool_autonomy_cycle import PoolAutonomyCycle, apply_to_packet, decide

PERSONA = {"persona_id": "p1", "tenant_id": "t1", "name": "P1", "mandate": "momentum"}


def member(ref, stage="active", joined="2026-10-01T00:00:00Z"):
    return {"artifact_ref": ref, "checksum": "sha256:" + "a" * 64, "stage": stage, "risk_weight": .5, "joined_at": joined}


def pool(*members, version=3):
    return {"persona_id": "p1", "tenant_id": "t1", "pool_version": version, "members": list(members)}


LOW = {"demand_reasons": ["low_risk_budget_utilisation"]}


class Ports:
    def __init__(self, *, candidates=(), recalled=(), demands=(), families=None, pool_now=None):
        self.cand, self.recalled, self.demands = list(candidates), set(recalled), list(demands)
        self.families, self.pool_now = families or {}, pool_now
        self.calls, self.memory, self.opened = [], [], []

    def current_pool(self, persona): return self.pool_now
    def reconcile_demands(self, persona, members): return self.demands
    def candidates(self, persona, pool): return self.cand
    def family(self, persona, ref): return self.families.get(ref)
    def recall_tried_families(self, persona): return self.recalled
    def missing_datasets(self, persona, pool): return ["tw-margin-balance"]
    def open_demand(self, persona, body):
        self.opened.append(body)
        return {"demand_id": "rd-1", "created": True}
    def admission_dataset(self, persona, candidate): return {"records": []}
    def decide_pool(self, persona, body):
        self.calls.append(body)
        if body["action"] == "admit":
            return {"pool_version": 4, "decision": {"status": "refused", "reasons": ["candidate_correlated_with_active_member"]}}
        return {"pool_version": 4, "decision": {"status": "applied"}, "members": [dict(member("a"), stage="removed")]}
    def propose_evolution(self, persona, pool, m): return {"status": "proposed", "decision_id": "evd-1"}
    def write_memory(self, persona, entry): self.memory.append(entry)


def observe(p, health=LOW):
    return {"pool": p, "pool_health": health, "unavailable": []}


def test_decision_open_demand_when_short_and_no_candidate():
    out = decide(pool=pool(member("a")), health=LOW, open_demands=[], candidates=[], tried=[], now_day="2026-11-12T00:00:00Z")
    assert out["kind"] == "open_demand" and out["short_measure"] == "gross_utilisation"


def test_decision_never_duplicates_an_open_demand():
    open_demand = [{"demand_id": "rd-1", "short_measure": "gross_utilisation", "status": "open"}]
    out = decide(pool=pool(member("a")), health=LOW, open_demands=open_demand, candidates=[], tried=[], now_day="2026-11-12T00:00:00Z")
    assert out["kind"] == "no_action" and out["reason"] == "demand_already_open_waiting_for_candidate"


def test_unknown_health_is_not_a_shortage_and_sufficient_pool_explores():
    unknown = decide(pool=pool(member("a")), health={"demand_reasons": ["health_inputs_unknown"]}, open_demands=[],
                     candidates=[], tried=[], now_day="2026-11-12T00:00:00Z")
    assert unknown["kind"] == "no_action" and "health_inputs_unknown" in unknown["reason"]
    healthy = decide(pool=pool(member("a")), health={"demand_reasons": []}, open_demands=[], candidates=[], tried=[],
                     now_day="2026-11-12T00:00:00Z")
    assert healthy["kind"] == "propose_mutate"


def test_widen_after_families_exhausted_on_low_independent_bets():
    health = {"demand_reasons": ["too_few_independent_bets"]}
    open_demand = [{"demand_id": "rd", "short_measure": "effective_bets", "status": "open"}]
    out = decide(pool=pool(member("a"), member("b")), health=health, open_demands=open_demand, candidates=[],
                 tried=["f1", "f2", "f3"], now_day="2026-11-12T00:00:00Z")
    assert out["kind"] == "propose_widen"


def test_old_trial_members_get_reweighted():
    out = decide(pool=pool(member("a"), member("t", "trial", "2026-10-01T00:00:00Z")), health={"demand_reasons": []},
                 open_demands=[], candidates=[], tried=[], now_day="2026-11-12T00:00:00Z")
    assert out["kind"] == "reweight"


def test_demand_carries_mandate_missing_datasets_tried_families_and_short_measure_and_excludes_failed_family_candidates():
    ports = Ports(recalled={"Momo"}, families={"a": "trend", "cand": "momo"},
                  candidates=[{"artifact_ref": "cand", "checksum": "c", "family": "momo"}])
    result = PoolAutonomyCycle(ports, now=lambda: "2026-11-12T00:00:00Z").run(PERSONA, observe(pool(member("a"))), "pk1")
    assert result["status"] == "completed" and result["decide"]["kind"] == "open_demand"
    body = ports.opened[0]
    assert body["mandate"] == "momentum" and body["missing_datasets"] == ["tw-margin-balance"]
    assert body["short_measure"] == "gross_utilisation" and body["tried_families"] == ["momo", "trend"]


def test_refused_admission_is_recorded_as_tried_and_goes_through_owner_route_paper_only():
    ports = Ports(families={"cand": "momo"}, candidates=[{"artifact_ref": "cand", "checksum": "c", "family": "momo"}])
    result = PoolAutonomyCycle(ports, now=lambda: "2026-11-12T00:00:00Z").run(PERSONA, observe(pool(member("a"))), "pk1")
    call = ports.calls[0]
    assert call["action"] == "admit" and call["execution_context"] == "paper" and call["expected_version"] == 3
    assert result["act"]["receipts"][0]["status"] == "refused"
    assert ports.memory[0]["outcome"] == "admission_refused" and ports.memory[0]["family"] == "momo"
    assert result["learn"]["tried_families"] == ["momo"]


def test_decision_uses_current_owner_pool_version_after_risk_reweight():
    ports = Ports(candidates=[{"artifact_ref": "cand", "checksum": "c"}], pool_now=pool(member("a"), version=7))
    PoolAutonomyCycle(ports, now=lambda: "2026-11-12T00:00:00Z").run(PERSONA, observe(pool(member("a"), version=3)), "pk1")
    assert ports.calls[0]["expected_version"] == 7


def test_missing_governed_dataset_fails_closed_without_owner_call():
    ports = Ports(candidates=[{"artifact_ref": "cand", "checksum": "c"}])
    ports.admission_dataset = lambda persona, c: (_ for _ in ()).throw(RuntimeError("nope"))
    result = PoolAutonomyCycle(ports, now=lambda: "2026-11-12T00:00:00Z").run(PERSONA, observe(pool(member("a"))), "pk1")
    assert ports.calls == [] and result["act"]["receipts"][0]["status"] == "not_attempted"


def test_persona_without_pool_is_skipped_with_reason_and_failure_is_isolated():
    cycle = PoolAutonomyCycle(Ports(), now=lambda: "2026-11-12T00:00:00Z")
    skipped = cycle.run(PERSONA, {"unavailable": ["strategy_pool"]}, "pk")
    assert skipped["status"] == "skipped" and skipped["reason"] == "no_strategy_pool"
    broken = Ports()
    broken.candidates = lambda *a: (_ for _ in ()).throw(RuntimeError("owner down"))
    failed = PoolAutonomyCycle(broken).run(PERSONA, observe(pool(member("a"))), "pk")
    assert failed["status"] == "failed" and "owner down" in failed["reason"]
    assert cycle.run(PERSONA, observe(pool(member("a"))), "pk")["status"] == "completed"  # next persona unaffected


def test_removed_member_is_learned_and_packet_gets_all_five_stages():
    ports = Ports(families={"a": "trend"})
    old = pool(member("a"), member("t", "trial", "2026-09-01T00:00:00Z"))
    result = PoolAutonomyCycle(ports, now=lambda: "2026-11-12T00:00:00Z").run(PERSONA, observe(old, {"demand_reasons": []}), "pk1")
    assert result["decide"]["kind"] == "remove" and result["learn"]["tried_families"] == ["trend"]
    packet = {"packet_id": "pk1", "observe": {"telemetry_refs": []}, "orient": {"evidence_bundle_refs": []}, "decide": {},
              "act": {}, "learn": {"telemetry_refs": []}, "audit_refs": []}
    apply_to_packet(packet, result)
    assert packet["decide"]["decision_rationale_ref"] == "pk1#pool_autonomy/decide" and packet["act"]["live_capital_side_effects"] is False
    assert packet["pool_autonomy"]["act"]["paper_only"] is True


def test_prior_risk_removal_is_persisted_and_excludes_family_and_candidate():
    """Removal by the deterministic risk owner earlier in the cycle becomes memory and excludes its family."""
    observed = pool(member("a"), member("b"))
    now = pool(member("b"), dict(member("a"), stage="removed"))
    ports = Ports(pool_now=now, families={"a": "fam-a", "b": "fam-b"},
                  candidates=[{"artifact_ref": "c", "checksum": "sha256:" + "c" * 64, "family": "fam-a"}])
    out = PoolAutonomyCycle(ports).run(PERSONA, observe(observed), "pk-1")
    assert out["status"] == "completed"
    assert [(e["outcome"], e["artifact_ref"], e["family"]) for e in ports.memory] == [("removed", "a", "fam-a")]
    assert out["decide"]["kind"] != "admit_trial"  # candidate from the removed family is excluded
    assert "fam-a" in out["orient"]["tried_families"]
    assert out["learn"]["tried_families"] == ["fam-a"]


def test_http_ports_use_owner_specific_credentials_and_fail_closed(monkeypatch):
    from unittest import mock
    from services.persona.pool_autonomy_cycle import HttpPoolCyclePorts, governed_dataset_loader
    seen = {}

    def fake_urlopen(request, timeout):
        seen[request.full_url] = request.get_header("Authorization")
        import io
        return io.StringIO("{}")

    env = {"PERSONA_RESEARCH_SERVICE_TOKEN": "r-tok", "PANTHEON_MEMORY_AUTH_TOKEN": "m-tok",
           "EVOLUTION_AUTH_TOKEN": "e-tok", "PERSONA_REGISTRY_SERVICE_TOKEN": "g-tok"}
    ports = HttpPoolCyclePorts(env=env)
    with mock.patch("services.persona.pool_autonomy_cycle.urlopen", fake_urlopen):
        for service in ("research", "memory", "evolution", "registry"):
            ports._urllib("GET", service, "/x", "t1")
    assert sorted(seen.values()) == ["Bearer e-tok", "Bearer g-tok", "Bearer m-tok", "Bearer r-tok"]
    with pytest.raises(RuntimeError, match="owner_credential_unconfigured:research"):
        HttpPoolCyclePorts(env={})._urllib("GET", "research", "/x", "t1")
    with pytest.raises(RuntimeError, match="governed_dataset_loader_unconfigured"):
        governed_dataset_loader({})(PERSONA, {"artifact_ref": "c"})
