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
        self.consult = {"state": "verified", "memo_id": "memo-1", "decision_id": "d-1", "request_id": "r-1"}
        self.consult_calls, self.operator_queue = [], []

    def current_pool(self, persona): return self.pool_now
    def reconcile_demands(self, persona, members): return self.demands
    def candidates(self, persona, pool): return self.cand
    def family(self, persona, ref): return self.families.get(ref)
    def recall_tried_families(self, persona): return self.recalled
    def missing_datasets(self, persona, pool): return ["tw-margin-balance"]
    def open_demand(self, persona, body):
        self.opened.append(body)
        return {"demand_id": "rd-1", "created": True}
    def trial_admission_consultation(self, persona, pool, cand, packet_id):
        self.consult_calls.append((cand["artifact_ref"], pool["pool_version"], packet_id))
        return self.consult
    def queue_operator_decision(self, persona, pool, cand, consult):
        self.operator_queue.append((cand["artifact_ref"], consult["reason"]))
        return {"decision_id": "op-1", "status": "queued"}
    def admission_dataset(self, persona, candidate): return {"records": []}
    def candidate_book(self, persona, candidate, dataset):
        return {"checksum": candidate["checksum"], "source_ref": "governed-replay:test", "since_inception": True}
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


def test_admit_sends_candidate_governed_book_and_learns_replaced_member():
    cand = {"artifact_ref": "c", "checksum": "ck", "family": "newfam"}
    ports = Ports(pool_now=pool(member("a")), candidates=[cand], families={"a": "oldfam"})
    def decide_pool(persona, body):
        ports.calls.append(body)
        return {"pool_version": 5, "decision": {"status": "applied", "replaced_artifact_ref": "a", "reasons": []},
                "members": [dict(member("a"), stage="removed"), member("c")]}
    ports.decide_pool = decide_pool
    out = PoolAutonomyCycle(ports, now=lambda: "2026-11-12T00:00:00Z").run(PERSONA, observe(pool(member("a"))), "pk-1")
    assert ports.calls[0]["books"]["c"]["source_ref"] == "governed-replay:test"
    removed = [e for e in ports.memory if e["outcome"] == "removed"]
    assert [(e["artifact_ref"], e["family"]) for e in removed] == [("a", "oldfam")]
    assert "oldfam" in out["learn"]["tried_families"]


def test_missing_candidate_book_refuses_without_owner_call():
    ports = Ports(pool_now=pool(member("a")), candidates=[{"artifact_ref": "c", "checksum": "ck", "family": "f"}])
    ports.candidate_book = lambda persona, c, d: (_ for _ in ()).throw(ValueError("backtest_dates_missing_or_duplicate"))
    out = PoolAutonomyCycle(ports, now=lambda: "2026-11-12T00:00:00Z").run(PERSONA, observe(pool(member("a"))), "pk-1")
    assert ports.calls == [] and out["act"]["receipts"][0]["reason"].startswith("candidate_book_unavailable")


def test_failed_pool_cycle_is_reported_as_cycle_failure_even_when_model_turn_succeeds(tmp_path):
    import json
    from types import SimpleNamespace
    from services.persona.persona_cycle_controller import PersonaCycleController

    output = {"orient": "No signal.", "proposals": [{"kind": "no_action", "reason": "none", "evidence_refs": []}],
              "memory_candidates": []}
    owners = SimpleNamespace(observe=lambda persona, since=None: {"unavailable": [], "telemetry_refs": [], "incident_refs": [],
                                                                  "human_feedback_refs": [], "harness_version": 1},
                             risk=lambda persona, observe, packet_id: {"status": "no_change"})
    controller = PersonaCycleController(None, owners, store_path=tmp_path / "packets.jsonl",
        turn_runner=lambda pid, prompt: SimpleNamespace(status="completed", persona_id=pid, decision=json.dumps(output),
                                                        to_dict=lambda: {}),
        pool_cycle=SimpleNamespace(run=lambda persona, observe, packet_id: {
            "persona_id": persona["persona_id"], "packet_id": packet_id, "status": "failed", "reason": "RuntimeError: boom"}))
    persona = {**PERSONA, "lifecycle_state": "paper_owner"}
    packet = controller.consume_run(persona, "job-1", {"runId": "run-1", "runAtMs": 1794477600000, "jobId": "job-1", "status": "ok"})
    assert packet["status"] == "observing"
    assert packet["persona_cycle"]["failure_reason"].startswith("pool_cycle_failed:RuntimeError")


def test_candidates_resolve_spec_admissions_to_approved_artifacts_and_unusable_entries_do_not_starve_supply():
    from services.persona.pool_autonomy_cycle import HttpPoolCyclePorts
    from services.evolution.test_approved_baselines import genuine_entry
    from services.research.tests.test_research_orchestrator_http_service import _load_service_module
    module = _load_service_module()
    good = {**genuine_entry(tenant="t1"), "artifact_type": "execution_bundle"}
    good["lineage"] = {**good["lineage"], "source_strategy_spec_id": "spec-good"}
    family = good["metadata"]["strategy_artifact"]["strategy_id"]

    def row(spec, queue_status, error=None):
        admission = {"tenant_id": "t1", "strategy_spec_id": spec, "strategy_id": family, "checksum": "sha256:" + "a" * 64, "status": "admitted"}
        return module._build_alpha_replication_entry(admission, {**admission, "status": queue_status, "failure_reason": error})
    rows = [row("spec-pending", "pending"), row("spec-dlq", "dlq", "boom"), row("spec-unlinked", "completed"), row("spec-good", "completed")]

    def transport(method, service, path, tenant, body=None):
        return rows if service == "research" else [{"entry": good, "deployment_stage": None}]
    out = HttpPoolCyclePorts(transport).candidates({"persona_id": "p1", "tenant_id": "t1"}, {"members": []})
    assert out == [{"artifact_ref": good["registry_id"], "checksum": good["checksum"], "family": family, "demand_ref": None}]
    # already-pooled artifacts are not re-offered
    assert HttpPoolCyclePorts(transport).candidates({"persona_id": "p1", "tenant_id": "t1"},
        {"members": [{"artifact_ref": good["registry_id"], "stage": "active"}]}) == []
    # an artifact without the approved gate proof is not a candidate
    good["replication_success"] = False
    assert HttpPoolCyclePorts(transport).candidates({"persona_id": "p1", "tenant_id": "t1"}, {"members": []}) == []


CAND = [{"artifact_ref": "cand", "checksum": "ck", "family": "momo"}]


def _run(ports, packet="pk-1"):
    return PoolAutonomyCycle(ports, now=lambda: "2026-11-12T00:00:00Z").run(PERSONA, observe(pool(member("a"))), packet)


def test_admission_decision_opens_consultation_and_does_not_admit_in_that_cycle():
    ports = Ports(candidates=CAND, families={"cand": "momo"})
    ports.consult = {"state": "opened", "decision_id": "d-1", "request_id": "r-1"}
    out = _run(ports)
    assert ports.calls == [] and out["decide"]["kind"] == "admit_trial"
    assert out["act"]["receipts"][0]["status"] == "consultation_opened"
    # pending is neither a refused admission nor a tried family
    assert [e["outcome"] for e in ports.memory] == ["admission_pending"]
    assert out["learn"]["tried_families"] == []
    assert not any(t.startswith("tried_family") for e in ports.memory for t in e.get("tags", []))
    assert "admission_refused" not in out["learn"]["memory_entries"]


def test_pending_consultation_is_recorded_pending_every_cycle_without_admission():
    ports = Ports(candidates=CAND)
    ports.consult = {"state": "pending", "decision_id": "d-1", "request_id": "r-1"}
    out = _run(ports)
    assert ports.calls == [] and out["act"]["receipts"][0]["status"] == "consultation_pending"
    assert out["learn"]["memory_entries"] == ["admission_pending"]


def test_verified_memo_admits_with_memo_id_as_persona_initiated():
    ports = Ports(candidates=CAND)
    out = _run(ports)
    call = ports.calls[0]
    assert call["initiated_by"] == "persona" and call["consultation_memo_id"] == "memo-1" and call["action"] == "admit"
    assert out["act"]["receipts"][0]["consultation_memo_id"] == "memo-1"


@pytest.mark.parametrize("reason", ["risk_persona_requires_operator", "panel_conditions_require_operator", "consultation_canceled"])
def test_oppose_or_ambiguous_answer_becomes_operator_decision_and_never_admits(reason):
    ports = Ports(candidates=CAND)
    ports.consult = {"state": "operator", "reason": reason, "decision_id": "d-1", "request_id": "r-1"}
    out = _run(ports)
    receipt = out["act"]["receipts"][0]
    assert ports.calls == [] and receipt["status"] == "operator_decision_required"
    assert receipt["operator_decision"]["status"] == "queued" and ports.operator_queue == [("cand", reason)]
    assert out["learn"]["memory_entries"] == ["admission_operator_decision"]


@pytest.mark.parametrize("break_it", ["missing_port", "raises"])
def test_missing_consultation_fails_closed_with_recorded_reason(break_it):
    ports = Ports(candidates=CAND)
    if break_it == "missing_port":
        del Ports.trial_admission_consultation
    else:
        ports.trial_admission_consultation = lambda *a: (_ for _ in ()).throw(RuntimeError("consultation down"))
    try:
        out = _run(ports)
    finally:
        if break_it == "missing_port":
            Ports.trial_admission_consultation = lambda self, persona, pool, cand, packet_id: self.consult
    receipt = out["act"]["receipts"][0]
    assert ports.calls == [] and receipt["status"] == "not_attempted" and receipt["reason"].startswith("consultation_unavailable")
    assert ports.memory == []


def test_http_ports_open_once_verify_published_memo_and_queue_operator_decision():
    """Port-level: one request per candidate+pool version; published memo goes through the shared verification."""
    from types import SimpleNamespace
    from services.persona import consult_triggers as ct
    from services.persona.pool_autonomy_cycle import HttpPoolCyclePorts
    opened, requests, memos, posts = [], [], [], []
    class Client:
        def list_requests(self, **kw): return list(requests)
        def list_memos(self, **kw): return list(memos)
        def open_persona_panel(self, requirement, **kw):
            opened.append((requirement, kw))
            requests.append({"request_id": kw["request_id"], "status": "submitted", "target_id": requirement.decision_id,
                             "from_persona_id": kw["persona_id"]})
            return {"request_id": kw["request_id"]}
    seen_actor = []
    def factory(tenant, actor=None):
        seen_actor.append(actor)
        return Client()
    def transport(method, service, path, tenant, body=None):
        posts.append((service, path, body)); return {}
    ports = HttpPoolCyclePorts(transport, consultation_factory=factory, env={})
    cand = {"artifact_ref": "cand", "checksum": "ck"}
    first = ports.trial_admission_consultation(PERSONA, pool(member("a"), version=3), cand, "pk-1")
    again = ports.trial_admission_consultation(PERSONA, pool(member("a"), version=3), cand, "pk-2")
    assert first["state"] == "opened" and again["state"] == "pending" and len(opened) == 1
    requirement, kw = opened[0]
    assert requirement.decision_type == "trial_admission" and requirement.risk_persona_id == ct.RISK_PERSONA_ID
    assert requirement.target_version == "3" and kw["persona_id"] == "p1" and "persona:p1" in seen_actor
    # a new pool version is a different decision id, so it is a different consultation
    assert ct.trial_admission_decision_id("t1", "p1", "cand", "ck", 4) != requirement.decision_id
    requests[0]["status"] = "published"
    assert ports.trial_admission_consultation(PERSONA, pool(member("a"), version=3), cand, "pk-3")["state"] == "pending"  # no memo yet
    memos.append({"memo_id": "memo-9"})
    original = ct.verify_published_memo
    try:
        ct.verify_published_memo = lambda *a, **k: ct.MemoCheck(False, "risk_persona_requires_operator")
        oppose = ports.trial_admission_consultation(PERSONA, pool(member("a"), version=3), cand, "pk-4")
        ct.verify_published_memo = lambda *a, **k: ct.MemoCheck(True)
        ok = ports.trial_admission_consultation(PERSONA, pool(member("a"), version=3), cand, "pk-5")
    finally:
        ct.verify_published_memo = original
    assert oppose["state"] == "operator" and ok == {"state": "verified", "memo_id": "memo-9", "decision_id": requirement.decision_id,
                                                       "request_id": requests[0]["request_id"]}
    queued = ports.queue_operator_decision(PERSONA, pool(member("a"), version=3), cand, oppose)
    assert queued["status"] == "queued" and posts[-1][1] == "/api/evolution/proposals"
    assert posts[-1][2]["metadata"]["operator_required"] is True and posts[-1][2]["metadata"]["candidate"]["artifact_ref"] == "cand"
