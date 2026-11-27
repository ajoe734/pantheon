"""Policy and isolation of the persona's daily pool cycle (fake ports; no network)."""
from __future__ import annotations

import os

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
        self.consult = {"state": "verified", "memo_id": "memo-1", "decision_id": "d-1", "request_id": "r-1", "pool_version": 3}
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
           "PANTHEON_MEMORY_SERVICE_TOKEN": "mr-tok", "EVOLUTION_AUTH_TOKEN": "e-tok", "PERSONA_REGISTRY_SERVICE_TOKEN": "g-tok"}
    ports = HttpPoolCyclePorts(env=env)
    with mock.patch("services.persona.pool_autonomy_cycle.urlopen", fake_urlopen):
        for service in ("research", "memory", "evolution", "registry"):
            ports._urllib("GET", service, "/x", "t1")
        ports._urllib("POST", "memory", "/y", "t1", {})
    # memory reads use the read-only reader principal; memory writes use the write principal
    assert seen["http://memory:8086/x"] == "Bearer mr-tok" and seen["http://memory:8086/y"] == "Bearer m-tok"
    assert sorted(v for k, v in seen.items() if "memory" not in k) == ["Bearer e-tok", "Bearer g-tok", "Bearer r-tok"]
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

    output = {"orient": "No signal.", "proposals": [{"kind": "no_action", "reason": "none", "evidence_refs": [],
                             "none_applicable_reason": "nothing retrieved applies"}],
              "memory_candidates": []}
    case_file = {"case_file_id": "casefile-stub", "digest": "d", "subject": {"decision_type": "research_demand"},
                 "lesson_query": {}, "groups": {"lessons": {"status": "ok", "items": [], "unavailable": []}}}
    owners = SimpleNamespace(case_file=lambda *args: case_file,
                             observe=lambda persona, since=None: {"unavailable": [], "telemetry_refs": [], "incident_refs": [],
                                                                  "human_feedback_refs": [], "harness_version": 1},
                             risk=lambda persona, observe, packet_id: {"status": "no_change"})
    controller = PersonaCycleController(None, owners, store_path=tmp_path / "packets.jsonl",
        tool_broker=SimpleNamespace(
            register_run=lambda pid, purpose, user: {"run_ref": "ptr-1"},
            run_record=lambda ref: {"run_ref": ref, "persona_id": PERSONA["persona_id"], "purpose": "daily_cycle",
                                    "run_id": "r1", "proposals": output["proposals"],
                                    "calls": [{"tool": "persona_proposal_submit", "outcome": "recorded"}]}),
        turn_runner=lambda pid, prompt, session_user=None: SimpleNamespace(status="completed", persona_id=pid,
                                                        decision="", to_dict=lambda: {}),
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
    assert call["consultation_pool_version"] == 3
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
                             "from_persona_id": kw["persona_id"], "metadata": {"panel": requirement.to_panel_metadata()}})
            return {"request_id": kw["request_id"]}
    seen_actor = []
    def factory(tenant, actor=None):
        seen_actor.append(actor)
        return Client()
    def transport(method, service, path, tenant, body=None):
        posts.append((service, path, body)); return {}
    versions = {3: pool(member("a"), version=3), 4: pool(member("a"), version=4), 5: pool(member("a"), member("z"), version=5)}
    store = SimpleNamespace(version=lambda pid, tenant, n: versions[n])
    ports = HttpPoolCyclePorts(transport, consultation_factory=factory, pool_store=store, env={})
    cand = {"artifact_ref": "cand", "checksum": "ck"}
    first = ports.trial_admission_consultation(PERSONA, pool(member("a"), version=3), cand, "pk-1")
    again = ports.trial_admission_consultation(PERSONA, pool(member("a"), version=3), cand, "pk-2")
    assert first["state"] == "opened" and again["state"] == "pending" and len(opened) == 1
    requirement, kw = opened[0]
    assert requirement.decision_type == "trial_admission" and requirement.risk_persona_id == ct.RISK_PERSONA_ID
    assert requirement.target_version == "3" and kw["persona_id"] == "p1" and "persona:p1" in seen_actor
    # the daily reweight appends a version: same live membership keeps the same consultation (no duplicate)...
    later = ports.trial_admission_consultation(PERSONA, versions[4], cand, "pk-2b")
    assert later["state"] == "pending" and later["pool_version"] == 3 and len(opened) == 1
    # ...while a membership change makes it a different decision and a new consultation
    changed = ports.trial_admission_consultation(PERSONA, versions[5], cand, "pk-2c")
    assert changed["state"] == "opened" and changed["pool_version"] == 5 and len(opened) == 2
    assert ct.trial_admission_decision_id("t1", "p1", "cand", "ck", 4) != requirement.decision_id
    requests.pop()  # drop the version-5 request again; the rest of the scenario is on version 3
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
                                                       "request_id": requests[0]["request_id"], "pool_version": 3}
    queued = ports.queue_operator_decision(PERSONA, pool(member("a"), version=3), cand, oppose)
    assert queued["status"] == "queued" and posts[-1][1] == "/api/evolution/proposals"
    assert posts[-1][2]["metadata"]["operator_required"] is True and posts[-1][2]["metadata"]["candidate"]["artifact_ref"] == "cand"


def test_demand_driven_candidate_is_returned_only_for_opening_persona_and_tenant():
    from services.persona.pool_autonomy_cycle import HttpPoolCyclePorts
    from services.evolution.test_approved_baselines import genuine_entry
    from services.research.tests.test_research_orchestrator_http_service import _load_service_module

    module = _load_service_module()
    bundle = {**genuine_entry(tenant="t1"), "artifact_type": "execution_bundle"}
    bundle["lineage"] = {**bundle["lineage"], "source_strategy_spec_id": "template-demand-spec-1"}
    bundle["metadata"] = {
        **bundle["metadata"],
        "persona_id": "p1",
        "demand_ref": "rd-open-1",
        "execution_context": "paper",
        "capital_scale_pct": 0,
    }
    family = bundle["metadata"]["strategy_artifact"]["strategy_id"]
    admission = {
        "tenant_id": "t1",
        "persona_id": "p1",
        "demand_ref": "rd-open-1",
        "strategy_spec_id": "template-demand-spec-1",
        "strategy_id": family,
        "checksum": bundle["checksum"],
        "status": "admitted",
    }
    row = module._build_alpha_replication_entry(admission, {**admission, "status": "completed"})

    def transport(method, service, path, tenant, body=None):
        if service == "research":
            return [row] if tenant == "t1" else []
        return [{"entry": bundle, "deployment_stage": None}] if tenant == "t1" else []

    ports = HttpPoolCyclePorts(transport)
    # Returned for the opening persona in the opening tenant with demand_ref preserved:
    assert ports.candidates({"persona_id": "p1", "tenant_id": "t1"}, {"members": []}) == [
        {
            "artifact_ref": bundle["registry_id"],
            "checksum": bundle["checksum"],
            "family": family,
            "demand_ref": "rd-open-1",
        }
    ]
    # Never returned for another persona in the same tenant:
    assert ports.candidates({"persona_id": "p2", "tenant_id": "t1"}, {"members": []}) == []
    # Never returned for another tenant:
    assert ports.candidates({"persona_id": "p1", "tenant_id": "t2"}, {"members": []}) == []


def test_persona_trial_count_charged_once_per_attempt_across_replays(monkeypatch):
    from pathlib import Path
    from types import SimpleNamespace
    from services.persona.pool_autonomy_cycle import HttpPoolCyclePorts
    from services.evolution.test_approved_baselines import genuine_entry
    from services.research.tests.test_research_orchestrator_http_service import _load_service_module

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "control-plane" / "persona"))
    module = _load_service_module()
    bundle = {**genuine_entry(tenant="t1"), "artifact_type": "execution_bundle"}
    bundle["lineage"] = {**bundle["lineage"], "source_strategy_spec_id": "template-demand-spec-2"}
    bundle["metadata"] = {**bundle["metadata"], "persona_id": "p1", "demand_ref": "rd-2"}
    family = bundle["metadata"]["strategy_artifact"]["strategy_id"]
    admission = {
        "tenant_id": "t1",
        "persona_id": "p1",
        "demand_ref": "rd-2",
        "strategy_spec_id": "template-demand-spec-2",
        "strategy_id": family,
        "checksum": bundle["checksum"],
        "status": "admitted",
    }
    row = module._build_alpha_replication_entry(admission, {**admission, "status": "completed"})

    versions = []
    apply_calls = []

    def apply_decision(persona_id, tenant_id, request, *, actor_id):
        apply_calls.append((persona_id, tenant_id, request.candidate.artifact_ref))
        version = {
            "pool_version": len(versions) + 1,
            "members": [],
            "decision": {
                "action": request.action,
                "status": "refused",
                "reasons": ["combined_oos_sharpe_did_not_improve"],
                "persona_trial_count": len(versions) + 1,
                "candidate": {"artifact_ref": request.candidate.artifact_ref, "checksum": request.candidate.checksum},
            },
        }
        versions.append(version)
        return version

    pool_store = SimpleNamespace(
        versions=lambda pid, tenant: list(versions),
        decide=apply_decision,
    )
    ports = HttpPoolCyclePorts(
        lambda method, service, path, tenant, body=None: (
            [row] if service == "research" else [{"entry": bundle, "deployment_stage": None}]
        ),
        pool_store=pool_store,
    )
    persona = {"persona_id": "p1", "tenant_id": "t1"}
    assert len(ports.candidates(persona, {"members": []})) == 1

    body = {
        "expected_version": 1,
        "execution_context": "paper",
        "action": "admit",
        "initiated_by": "persona",
        "consultation_memo_id": "memo-1",
        "consultation_pool_version": 1,
        "candidate": {"artifact_ref": bundle["registry_id"], "checksum": bundle["checksum"], "demand_ref": "rd-2"},
        "books": {},
        "observations": [],
        "dataset": {},
    }
    first = ports.decide_pool(persona, body)
    assert first["decision"]["persona_trial_count"] == 1
    assert len(apply_calls) == 1

    # Replay of decide_pool for the same candidate returns the recorded decision without re-charging:
    second = ports.decide_pool(persona, body)
    assert second == first
    assert len(apply_calls) == 1

    # Subsequent candidate reads exclude the already-decided candidate even when it was refused:
    assert ports.candidates(persona, {"members": []}) == []



# --- automatic publication (R6) -------------------------------------------------------------
class PublishPorts(Ports):
    def __init__(self, history, **kw):
        super().__init__(**kw)
        self.history, self.published, self.fail = history, [], None
        self.consult_state = {"state": "verified", "memo_id": "memo-e", "decision_id": "e-1", "request_id": "r-e"}
        self.consults = []
    def publication_state(self, persona): return self.history
    def envelope_publication_consultation(self, persona, pool, packet_id):
        self.consults.append(pool["pool_version"])
        return self.consult_state
    def publish_pool(self, persona, version):
        if self.fail:
            raise self.fail
        self.published.append(version)
        return {"status": "published", "envelope_artifact_id": "env-%d" % version, "deployment_plan_id": "plan-%d" % version}


@pytest.mark.parametrize("result", ["dispatched", "failed_read"])
def test_pending_dispatch_retries_exact_version_without_membership_gate(result):
    history = pins(("a", 1.0, False, "active"), version=2)
    history["published"]["2"]["status"] = "dispatched"
    ports = PublishPorts(history, pool_now=live_pool(6, ("a", 1.0, "active")))
    tried = []
    def publish(persona, version):
        tried.append(version)
        if result == "failed_read":
            raise RuntimeError("owner_down")
        return {"status": "dispatched"}
    ports.publish_pool = publish
    for _ in range(2):
        out = run_publish(ports, ports.pool_now)
        assert out["status"] == "publication_dispatch_unconfirmed"
        assert out["reasons"] == ["publication_dispatch_unconfirmed"]
    assert tried == [2, 2] and ports.consults == []


def test_hosted_v2_dispatch_confirmed_then_v6_admission_only_republish():
    history = pins(("a", 1.0, False, "active"), joined=None, version=2)
    history["published"]["2"]["status"] = "dispatched"
    ports = PublishPorts(history, pool_now=live_pool(6, ("a", 1.0, "active")))
    out = run_publish(ports, ports.pool_now)
    assert out["status"] == "published" and out["publication_pending"] and ports.published == [2]
    assert out["reasons"] == ["envelope_member_admission_missing:a"] and ports.consults == []
    history["published"]["2"]["status"] = "published"
    assert run_publish(ports, ports.pool_now)["status"] == "published" and ports.published == [2, 6]


def test_confirmed_dispatch_does_not_bypass_real_membership_consultation():
    history = pins(("a", 1.0, False, "active"), version=2)
    history["published"]["2"]["status"] = "dispatched"
    ports = PublishPorts(history, pool_now=live_pool(6, ("a", .9, "active"), ("b", .1, "trial")))
    ports.consult_state = {"state": "pending", "request_id": "r", "decision_id": "d"}
    out = run_publish(ports, ports.pool_now)
    assert out["status"] == "consultation_pending" and ports.published == [2] and ports.consults == [6]


JOINED = "2026-10-01T00:00:00Z"


def pins(*items, joined=JOINED, version=2):
    # A publish record written since the publisher carries joined_at (POOL-MEMBER-JOINED-AT-CARRY-20261126);
    # joined=None is a record from before, whose pins have no admission time.
    return {"published": {str(version): {"status": "published", "pool_version": version, "members": [
        {"artifact_ref": r, "risk_weight": w, "virtual_only": v, "stage": s, **({"joined_at": joined} if joined else {})}
        for r, w, v, s in items]}}}


def live_pool(version, *items):
    return {"persona_id": "p1", "tenant_id": "t1", "pool_version": version, "members": [
        {"artifact_ref": r, "checksum": "ck", "stage": s, "risk_weight": w, "joined_at": JOINED} for r, w, s in items]}


def run_publish(ports, p):
    return PoolAutonomyCycle(ports).run(PERSONA, observe(p, {"demand_reasons": []}), "pk-1")["act"]["publication"]


def test_unchanged_daily_reweight_publishes_nothing():
    ports = PublishPorts(pins(("a", .6, False, "active"), ("b", .4, False, "active")),
                         pool_now=live_pool(3, ("a", .61, "active"), ("b", .39, "active")))
    out = run_publish(ports, ports.pool_now)
    assert out["status"] == "unchanged" and ports.published == [] and ports.consults == []


def test_weight_move_beyond_tolerance_publishes_without_consultation():
    ports = PublishPorts(pins(("a", .6, False, "active"), ("b", .4, False, "active")),
                         pool_now=live_pool(3, ("a", .8, "active"), ("b", .2, "active")))
    out = run_publish(ports, ports.pool_now)
    assert out["status"] == "published" and ports.published == [3] and ports.consults == []
    assert out["reasons"] == ["weight_changed:a", "weight_changed:b"] and out["envelope_artifact_id"] == "env-3"


def test_trial_promotion_is_a_stage_change_and_publishes():
    ports = PublishPorts(pins(("a", .6, False, "active"), ("t", .1, False, "trial")),
                         pool_now=live_pool(3, ("a", .6, "active"), ("t", .1, "active")))
    out = run_publish(ports, ports.pool_now)
    assert out["status"] == "published" and out["reasons"] == ["stage_changed:t"] and ports.consults == []


def test_membership_change_needs_the_verified_memo_and_proceeds_with_it():
    held = PublishPorts(pins(("a", 1.0, False, "active")), pool_now=live_pool(3, ("a", .9, "active"), ("n", 0, "trial")))
    held.consult_state = {"state": "pending", "decision_id": "e-1", "request_id": "r-e"}
    out = run_publish(held, held.pool_now)
    assert out["status"] == "consultation_pending" and held.published == []
    operator = PublishPorts(pins(("a", 1.0, False, "active")), pool_now=held.pool_now)
    operator.consult_state = {"state": "operator", "reason": "risk_persona_requires_operator"}
    out = run_publish(operator, operator.pool_now)
    assert out["status"] == "consultation_operator" and out["reason"] == "risk_persona_requires_operator" and operator.published == []
    ok = PublishPorts(pins(("a", 1.0, False, "active")), pool_now=held.pool_now)
    out = run_publish(ok, ok.pool_now)
    assert out["status"] == "published" and out["consultation_memo_id"] == "memo-e" and ok.published == [3]


def test_failed_publish_is_reported_on_the_record_and_retried_by_the_next_cycle():
    ports = PublishPorts(pins(("a", .6, False, "active"), ("b", .4, False, "active")),
                         pool_now=live_pool(3, ("a", .8, "active"), ("b", .2, "active")))
    ports.fail = RuntimeError("owner_unavailable")
    result = PoolAutonomyCycle(ports).run(PERSONA, observe(ports.pool_now, {"demand_reasons": []}), "pk-1")
    assert result["act"]["publication"]["status"] == "failed" and result["act"]["publication"]["step"] == "publish"
    packet = {"packet_id": "pk-1", "audit_refs": [], "observe": {"telemetry_refs": []}, "orient": {"evidence_bundle_refs": []},
              "decide": {}, "act": {}, "learn": {"telemetry_refs": []}}
    apply_to_packet(packet, result)
    assert any(ref.startswith("pool-publication:failed:RuntimeError: owner_unavailable") for ref in packet["audit_refs"])
    ports.fail = None  # history still shows v2 as the last confirmed publication, so the next cycle retries
    assert run_publish(ports, ports.pool_now)["status"] == "published" and ports.published == [3]


def test_unreadable_publication_state_fails_closed_without_publishing():
    ports = PublishPorts({}, pool_now=live_pool(3, ("a", .6, "active")))
    ports.publication_state = lambda persona: (_ for _ in ()).throw(RuntimeError("bff_down"))
    out = run_publish(ports, ports.pool_now)
    assert out["status"] == "failed" and out["step"] == "compare" and ports.published == []


class RepublishPorts(PublishPorts):
    """The owner appends a version (identical members) and the publisher records pins with joined_at."""

    def __init__(self, history, **kw):
        super().__init__(history, **kw)
        self.versions, self.republish_calls = [self.pool_now], []

    def current_pool(self, persona): return self.versions[-1]

    def republish_pool_version(self, persona, pool, reasons):
        self.republish_calls.append((pool["pool_version"], list(reasons)))
        assert pool is self.versions[-1]
        self.versions.append({**pool, "pool_version": pool["pool_version"] + 1, "members": [dict(m) for m in pool["members"]]})
        return self.versions[-1]

    def publish_pool(self, persona, version):
        receipt = super().publish_pool(persona, version)
        [published] = [v for v in self.versions if v["pool_version"] == version]
        self.history["published"][str(version)] = {"status": "published", "pool_version": version, "members": [
            {"artifact_ref": m["artifact_ref"], "risk_weight": m["risk_weight"], "virtual_only": False,
             "stage": m["stage"], "joined_at": m.get("joined_at")} for m in published["members"] if m["stage"] != "removed"]}
        return receipt


def test_running_envelope_without_joined_at_republishes_once_with_one_new_version():
    ports = RepublishPorts(pins(("a", .6, False, "active"), ("b", .4, False, "active"), joined=None, version=3),
                           pool_now=live_pool(3, ("a", .6, "active"), ("b", .4, "active")))
    first = run_publish(ports, ports.pool_now)
    assert first["status"] == "published" and first["republished_version"] == 4 and ports.published == [4]
    assert first["reasons"] == ["envelope_member_admission_missing:a", "envelope_member_admission_missing:b"]
    assert ports.consults == []  # never a membership change: no consultation, no admission
    assert [m for m in ports.versions[-1]["members"]] == ports.versions[0]["members"]  # no reweight
    second = run_publish(ports, ports.versions[-1])
    assert second["status"] == "unchanged" and second["reasons"] == []
    assert len(ports.republish_calls) == 1 and ports.published == [4] and len(ports.versions) == 2


def test_newer_pool_version_already_present_publishes_it_without_appending():
    # Hosted: refused reweights already appended versions newer than the running envelope.
    ports = RepublishPorts(pins(("a", 1.0, False, "active"), joined=None, version=3),
                           pool_now=live_pool(7, ("a", 1.0, "active")))
    out = run_publish(ports, ports.pool_now)
    assert out["status"] == "published" and ports.published == [7] and ports.republish_calls == []
    assert "republished_version" not in out and run_publish(ports, ports.pool_now)["status"] == "unchanged"


def test_failed_republish_is_retried_without_a_second_version():
    ports = RepublishPorts(pins(("a", 1.0, False, "active"), joined=None, version=3),
                           pool_now=live_pool(3, ("a", 1.0, "active")))
    ports.fail = RuntimeError("owner_unavailable")
    assert run_publish(ports, ports.pool_now)["status"] == "failed" and len(ports.versions) == 2
    ports.fail = None
    out = run_publish(ports, ports.versions[-1])
    assert out["status"] == "published" and ports.published == [4] and len(ports.republish_calls) == 1


def test_admission_missing_with_a_real_change_publishes_the_change_only():
    ports = RepublishPorts(pins(("a", .6, False, "active"), ("b", .4, False, "active"), joined=None, version=2),
                           pool_now=live_pool(3, ("a", .8, "active"), ("b", .2, "active")))
    out = run_publish(ports, ports.pool_now)
    assert out["status"] == "published" and ports.published == [3] and ports.republish_calls == []


def test_republish_without_owner_port_is_not_attempted():
    ports = PublishPorts(pins(("a", 1.0, False, "active"), joined=None, version=3), pool_now=live_pool(3, ("a", 1.0, "active")))
    out = run_publish(ports, ports.pool_now)
    assert out["status"] == "not_attempted" and out["reason"] == "republish_port_unconfigured" and ports.published == []


def test_unconfigured_publish_port_is_recorded_not_guessed():
    ports = Ports(pool_now=live_pool(3, ("a", .6, "active")))
    assert run_publish(ports, ports.pool_now) == {"status": "not_attempted", "reason": "publish_port_unconfigured"}


# --- escalated envelope publication -> one operator decision the next cycle consumes ----------------------------
# Real HttpPoolCyclePorts (PoolPublishPort) and the real publication code; only the owners are doubles: the
# consultation client, the persona pool store, the BFF publish routes and the evolution governance queue.

class _OwnerError(Exception):
    def __init__(self, code, detail=""):
        super().__init__(f"HTTP {code}: {detail}")
        self.code = code


class GovernanceDouble:
    """The evolution owner's proposal queue as Management drives it (review -> approve / reject)."""

    def __init__(self):
        self.proposals, self.posts, self.reads = {}, [], 0

    def post(self, body):
        self.posts.append(body)
        if body["decision_id"] in self.proposals:  # evolution main.py: occupied decision_id -> 409
            raise _OwnerError(409, "decision_id is already occupied by an evolution decision")
        self.proposals[body["decision_id"]] = {**body, "decision_state": "proposed", "review_chain": [],
                                               "approval_decision_id": None}
        return self.proposals[body["decision_id"]]

    def get(self, decision_id):
        self.reads += 1
        if decision_id not in self.proposals:
            raise _OwnerError(404, "not found")
        return self.proposals[decision_id]

    def operator(self, decision_id, action, note):
        record = self.proposals[decision_id]
        record["review_chain"].append({"step_type": "reviewed", "actor_role": "risk_owner", "actor_id": "op-1",
                                       "note": "seen"})
        record["approval_decision_id"] = "appr-1"
        record["decision_state"] = {"approve": "approved", "reject": "rejected"}[action]
        record["review_chain"].append({"step_type": record["decision_state"], "actor_role": "risk_owner",
                                       "actor_id": "op-1", "note": note})


def _escalated_world(monkeypatch, *, pool_version=5):
    from types import SimpleNamespace
    from services.persona import consult_triggers as ct
    from services.persona.pool_autonomy_cycle import HttpPoolCyclePorts
    from services.persona.pool_publish_port import envelope_publication_requirement

    world = SimpleNamespace(governance=GovernanceDouble(), published=[], opened=[],
                            pool=live_pool(pool_version, ("a", .9, "active"), ("n", .1, "trial")),
                            history=pins(("a", 1.0, False, "active"), version=2))
    requirement = envelope_publication_requirement("t1", "p1", world.pool, 4)  # the panel reviewed v4 (same membership)
    request = {"request_id": "consult-" + requirement.decision_id, "status": "published", "from_persona_id": "p1",
               "metadata": {"panel": requirement.to_panel_metadata()}}

    class Consultation:
        def list_requests(self, **kw): return [request]
        def list_memos(self, **kw): return [{"memo_id": "consult-envelope-publication-memo-1"}]
        def open_persona_panel(self, requirement, **kw):
            world.opened.append(kw["request_id"])
            return {"request_id": kw["request_id"]}

    monkeypatch.setattr(ct, "verify_published_memo", lambda *a, **k: ct.MemoCheck(False, "risk_persona_requires_operator"))

    def transport(method, service, path, tenant, body=None):
        assert tenant == "t1"
        if service == "evolution" and method == "POST" and path == "/api/evolution/proposals":
            return world.governance.post(body)
        if service == "evolution" and method == "GET" and path.startswith("/api/evolution/proposals/"):
            return world.governance.get(path.rsplit("/", 1)[1])
        if service == "evolution" and method == "GET" and path.startswith("/api/evolution/proposals?"):
            return list(world.governance.proposals.values())
        if service == "bff" and method == "GET" and path.endswith("/publish-history"):
            return {"data": world.history}
        if service == "bff" and method == "POST" and path.endswith("/strategy-pool/publish"):
            world.published.append(body)
            return {"data": {"status": "published", "envelope_artifact_id": "env-%d" % body["pool_version"],
                             "deployment_plan_id": "plan-1"}}
        raise AssertionError((method, service, path))

    store = SimpleNamespace(current=lambda pid, tenant: world.pool)
    world.ports = HttpPoolCyclePorts(transport, pool_store=store, consultation_factory=lambda t, a=None: Consultation(), env={})
    world.cycle = PoolAutonomyCycle(world.ports)
    world.consult_decision_id = requirement.decision_id
    return world


def test_escalated_publication_queues_one_operator_decision_with_memo_reason_and_pool_version(monkeypatch):
    world = _escalated_world(monkeypatch)
    first = world.cycle._publish(PERSONA, "pk-1")
    assert first["status"] == "consultation_operator" and first["reason"] == "risk_persona_requires_operator"
    assert first["operator_decision"]["status"] == "queued" and world.published == []
    [body] = world.governance.posts
    assert body["action_type"] == "flag_for_review" and body["target_type"] == "persona" and body["target_id"] == "p1"
    assert body["persona_id"] == "p1" and body["target_version"] == "4"
    assert body["metadata"]["consultation_memo_id"] == "consult-envelope-publication-memo-1"
    assert body["metadata"]["reason"] == "risk_persona_requires_operator" and body["metadata"]["pool_version"] == 4
    assert body["metadata"]["consultation_decision_id"] == world.consult_decision_id
    assert first["operator_decision"]["decision_id"] == body["decision_id"]
    # Repeat cycles (and the daily re-weight's newer version with the same membership) never queue a duplicate.
    world.pool = {**world.pool, "pool_version": 6}
    for packet in ("pk-2", "pk-3"):
        again = world.cycle._publish(PERSONA, packet)
        assert again["status"] == "consultation_operator"
        assert again["operator_decision"] == {"decision_id": body["decision_id"], "status": "awaiting_operator",
                                              "decision_state": "proposed"}
    assert len(world.governance.posts) == 1 and len(world.governance.proposals) == 1 and world.published == []
    assert world.opened == []


def test_operator_approval_is_consumed_by_the_next_cycle_and_recorded_on_the_receipt(monkeypatch):
    world = _escalated_world(monkeypatch)
    decision_id = world.cycle._publish(PERSONA, "pk-1")["operator_decision"]["decision_id"]
    world.governance.operator(decision_id, "approve", "ok to publish")
    out = world.cycle._publish(PERSONA, "pk-2")
    assert out["status"] == "published" and out["operator_decision_id"] == decision_id
    assert out["pool_version"] == 5 and out["envelope_artifact_id"] == "env-5"
    assert world.published == [{"pool_version": 5, "stage": "paper", "defer_confirmation": True}] and len(world.governance.posts) == 1


def test_operator_rejection_keeps_the_previous_envelope_and_records_the_reason(monkeypatch):
    world = _escalated_world(monkeypatch)
    decision_id = world.cycle._publish(PERSONA, "pk-1")["operator_decision"]["decision_id"]
    world.governance.operator(decision_id, "reject", "book observations insufficient")
    result = {"status": "completed", "persona_id": "p1",
              "act": {"publication": world.cycle._publish(PERSONA, "pk-2")}}
    out = result["act"]["publication"]
    assert out["status"] == "operator_rejected" and out["reason"] == "book observations insufficient"
    assert out["operator_decision"] == {"decision_id": decision_id, "status": "rejected", "actor_id": "op-1"}
    assert world.published == [] and len(world.governance.posts) == 1  # no re-queue for this pool version
    packet = {"packet_id": "pk-2", "audit_refs": [], "observe": {"telemetry_refs": []}, "orient": {"evidence_bundle_refs": []},
              "decide": {}, "act": {}, "learn": {"telemetry_refs": []}}
    apply_to_packet(packet, {**result, "observe": {}, "orient": {}})
    assert "pool-publication:operator_rejected:book observations insufficient" in packet["audit_refs"]
    assert world.cycle._publish(PERSONA, "pk-3")["status"] == "operator_rejected" and len(world.governance.posts) == 1


def test_operator_decision_of_another_consultation_is_never_taken_as_approval(monkeypatch):
    world = _escalated_world(monkeypatch)
    decision_id = world.cycle._publish(PERSONA, "pk-1")["operator_decision"]["decision_id"]
    world.governance.operator(decision_id, "approve", "ok")
    world.governance.proposals[decision_id]["metadata"]["consultation_decision_id"] = "envelope-publication-other"
    out = world.cycle._publish(PERSONA, "pk-2")
    assert out["status"] == "consultation_operator" and out["operator_decision"]["status"] == "read_failed"
    assert "operator_decision_binding_mismatch" in out["operator_decision"]["error"] and world.published == []


def test_unreadable_or_unqueueable_governance_keeps_the_publication_held(monkeypatch):
    world = _escalated_world(monkeypatch)
    world.governance.post = lambda body: (_ for _ in ()).throw(_OwnerError(503, "down"))
    out = world.cycle._publish(PERSONA, "pk-1")
    assert out["status"] == "consultation_operator" and out["operator_decision"]["status"] == "queue_failed"
    world.governance.get = lambda decision_id: (_ for _ in ()).throw(_OwnerError(503, "down"))
    out = world.cycle._publish(PERSONA, "pk-2")
    assert out["operator_decision"]["status"] == "read_failed" and world.published == []


def test_unchanged_membership_needs_no_consultation_or_operator_decision(monkeypatch):
    world = _escalated_world(monkeypatch)
    world.history = pins(("a", .9, False, "active"), ("n", .1, False, "trial"), version=4)
    assert world.cycle._publish(PERSONA, "pk-1")["status"] == "unchanged"
    world.pool = live_pool(5, ("a", .5, "active"), ("n", .5, "trial"))  # weight move beyond tolerance, same members
    out = world.cycle._publish(PERSONA, "pk-2")
    assert out["status"] == "published" and "operator_decision_id" not in out and "consultation_memo_id" not in out
    assert world.governance.posts == [] and world.governance.reads == 0 and world.opened == []


# --- Persona memory principals against the real memory route gates (only HTTP transport is faked) -------------------

def _memory_gate_harness(monkeypatch, tmp_path):
    import io
    import time
    import urllib.error
    from fastapi.testclient import TestClient
    from services.memory import main as memory_main
    from services.runtime_auth_inbound import encode_jwt_hs256

    secret = "persona-memory-gate-secret-123456"
    for key, value in {"PANTHEON_MEMORY_STORE_PATH": tmp_path / "m.json", "PANTHEON_PERSONA_MEMORY_STORE_PATH": tmp_path / "p.json",
                       "PANTHEON_MEMORY_STORE": tmp_path / "m.json", "PANTHEON_PERSONA_MEMORY_STORE": tmp_path / "p.json"}.items():
        monkeypatch.setenv(key, str(value))
    monkeypatch.setenv("PANTHEON_MEMORY_AUTHZ_MODE", "local")
    monkeypatch.setenv("PANTHEON_MEMORY_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_MEMORY_JWT_SECRET", secret)
    memory_main._STORE = None
    memory_main._PERSONA_STORE = None
    client = TestClient(memory_main.app)

    def token(sub, roles):
        return encode_jwt_hs256({"sub": sub, "roles": roles, "tenant_id": "t1", "exp": int(time.time()) + 3600}, secret=secret)

    def fake_urlopen(request, timeout=None):
        url = request.full_url.replace("http://memory:8086", "")
        headers = {k: v for k, v in request.header_items()}
        if request.get_method() == "POST":
            response = client.post(url, content=request.data, headers=headers)
        else:
            response = client.get(url, headers=headers)
        if response.status_code >= 400:
            raise urllib.error.HTTPError(request.full_url, response.status_code, "err", {}, io.BytesIO(response.content))
        return io.BytesIO(response.content)

    return client, token, fake_urlopen


def test_persona_memory_principals_are_admitted_or_refused_at_real_memory_gates(monkeypatch, tmp_path):
    import urllib.error
    from unittest import mock
    from services.knowledge.case_file import Owners, http_json
    from services.persona.pool_autonomy_cycle import HttpPoolCyclePorts

    client, token, fake_urlopen = _memory_gate_harness(monkeypatch, tmp_path)
    reader, writer = token("persona_cycle_controller", ["persona_session"]), token("persona-svc", ["persona-svc"])
    env = {"MEMORY_API_URL": "http://memory:8086", "PANTHEON_MEMORY_SERVICE_TOKEN": reader, "PANTHEON_MEMORY_AUTH_TOKEN": writer}
    ports = HttpPoolCyclePorts(env=env)
    persona = {"persona_id": "persona-a", "tenant_id": "t1"}
    entry = {"packet_id": "pk1", "outcome": "admission_refused", "family": "momentum", "artifact_ref": "art-1"}

    with mock.patch("services.persona.pool_autonomy_cycle.urlopen", fake_urlopen), \
            mock.patch("urllib.request.urlopen", fake_urlopen):
        # Writer principal: pool autonomy memory port write is admitted.
        assert ports.write_memory(persona, entry)
        # Reader principal: own-persona listing is admitted and returns the written entry's tag.
        assert "momentum" in ports.recall_tried_families(persona)
        # Another persona's listing through the same read principal is refused by governance.
        with pytest.raises(urllib.error.HTTPError) as other:
            ports._urllib("GET", "memory", "/api/memory/entries?contributing_persona_id=persona-b&session_persona_id=persona-a", "t1")
        assert other.value.code == 403
        # persona_session has no write role: the same route refuses it.
        with pytest.raises(urllib.error.HTTPError) as denied:
            HttpPoolCyclePorts(env=dict(env, PANTHEON_MEMORY_AUTH_TOKEN=reader)).write_memory(persona, entry)
        assert denied.value.code == 403
        # Case-file reader (real http_json entry point): similar_history/lessons retrieve is admitted for own persona.
        owners = Owners.from_env({"PANTHEON_MEMORY_API_URL": "http://memory:8086", "PANTHEON_MEMORY_SERVICE_TOKEN": reader})
        base = [("actor_id", "persona_cycle_controller"), ("session_id", "s1"), ("scope", "persona"), ("query", "momentum"),
                ("actor_roles", "persona_session"), ("persona_id", "persona-a")]
        assert http_json(owners.memory, "/api/memory/retrieve", tenant_id="t1", params=base + [("session_persona_id", "persona-a")]) is not None
        from services.knowledge.case_file import OwnerUnavailable
        with pytest.raises(OwnerUnavailable, match="http_403"):
            http_json(owners.memory, "/api/memory/retrieve", tenant_id="t1", params=base + [("session_persona_id", "persona-b")])
        # The write principal is not a reader.
        owners_w = Owners.from_env({"PANTHEON_MEMORY_API_URL": "http://memory:8086", "PANTHEON_MEMORY_SERVICE_TOKEN": writer})
        with pytest.raises(OwnerUnavailable, match="http_403"):
            http_json(owners_w.memory, "/api/memory/retrieve", tenant_id="t1", params=base + [("session_persona_id", "persona-a")])


def test_harness_and_feedback_bridge_writers_use_write_principal_at_real_route_gate(monkeypatch, tmp_path):
    import urllib.error
    from unittest import mock
    from services.persona.harness_adoption import HarnessAdoption
    from services.persona.learning_feedback_bridge import _memory_headers

    client, token, fake_urlopen = _memory_gate_harness(monkeypatch, tmp_path)
    reader, writer = token("persona_cycle_controller", ["persona_session"]), token("persona-svc", ["persona-svc"])
    monkeypatch.setenv("PANTHEON_MEMORY_API_URL", "http://memory:8086")
    payload = {"tenant_id": "t1", "source_event_type": "harness_experience", "write_authority": "persona-svc"}
    with mock.patch("services.persona.harness_adoption.urlopen", fake_urlopen):
        monkeypatch.setenv("PANTHEON_MEMORY_AUTH_TOKEN", reader)
        with pytest.raises(urllib.error.HTTPError) as denied:
            HarnessAdoption._send("memory", payload)
        assert denied.value.code == 403  # persona_session holds no write role
        monkeypatch.setenv("PANTHEON_MEMORY_AUTH_TOKEN", writer)
        with pytest.raises(urllib.error.HTTPError) as admitted:
            HarnessAdoption._send("memory", payload)
        assert admitted.value.code == 422  # past the role gate; the incomplete payload is refused by the writer
    for tok, expected in ((reader, 403), (writer, 422)):
        monkeypatch.setenv("PANTHEON_MEMORY_AUTH_TOKEN", tok)
        response = client.post("/api/memory/writebacks/learn-feedback", json=payload, headers=_memory_headers())
        assert response.status_code == expected


def test_bff_publish_principal_is_read_from_file_first_and_fails_closed(tmp_path):
    import io
    from unittest import mock
    from services.persona.pool_autonomy_cycle import HttpPoolCyclePorts
    token_file = tmp_path / "PERSONA_POOL_PUBLISH_BFF_TOKEN"
    token_file.write_text("bff-file-tok\n")
    token_file.chmod(0o400)
    seen = {}

    def fake_urlopen(request, timeout):
        seen[request.full_url] = request.get_header("Authorization")
        return io.StringIO("{}")

    env = {"PERSONA_POOL_PUBLISH_BFF_TOKEN_FILE": str(token_file), "PERSONA_POOL_PUBLISH_BFF_TOKEN": "env-tok"}
    with mock.patch("services.persona.pool_autonomy_cycle.urlopen", fake_urlopen):
        HttpPoolCyclePorts(env=env)._urllib("POST", "bff", "/bff/x", "t1", {})
    assert seen["http://operator-bff:8001/bff/x"] == "Bearer bff-file-tok"  # file wins over env
    # A configured but missing file fails closed; it never falls back to the env value.
    token_file.unlink()
    with pytest.raises(RuntimeError, match="Configured service credential unavailable"):
        HttpPoolCyclePorts(env=env)._urllib("POST", "bff", "/bff/x", "t1", {})
    with pytest.raises(RuntimeError, match="owner_credential_unconfigured:bff"):
        HttpPoolCyclePorts(env={})._urllib("POST", "bff", "/bff/x", "t1", {})


def test_partial_seed_read_is_recorded_as_partial_never_as_no_match(tmp_path):
    from services.persona.pool_autonomy_cycle import HttpPoolCyclePorts

    class Seeds(list):
        partial, pages, stop_reason = True, 20, "page_cap"

    class Module:
        @staticmethod
        def discover_for_demand(*a, **k):
            return {"matches": [], "excluded": [], "reason": "no_candidate_fits_persona", "candidates_considered": 3,
                    "backfill_datasets": ["ohlcv"]}
    import importlib
    real = importlib.import_module
    ports = HttpPoolCyclePorts(lambda *a, **k: {"items": [], "next_page_token": None},
                               discovery_loader=lambda p: {"seeds": Seeds([{}, {}, {}]), "route_policy": {}, "capability_snapshot": {}},
                               env={})
    import unittest.mock as mock
    with mock.patch("importlib.import_module", lambda n, *a: Module if n.endswith("persona_strategy_discovery") else real(n, *a)):
        found = ports.discover_for_demand({"persona_id": "p", "tenant_id": "t"}, {"demand_id": "d1"})
    assert found["partial"] and found["seeds_seen"] == 3 and found["partial_reason"] == "page_cap"
    assert found["reason"].startswith("partial_seed_read:3_seeds_seen")



# --- POOL-CYCLE-SPEC-READ-BOUNDED-20261125: bounded, paged registry StrategySpec read ---------------------

from urllib.parse import parse_qs, urlsplit  # noqa: E402

DISCOVERY_PATH = "/api/registry/strategy-specs"


class FakeRegistry:
    """The registry owner's ``view=discovery_candidates`` contract; any unpaged list read fails the test."""

    def __init__(self, entries, *, fail_on_page=None, page_seconds=0.0, clock=None):
        self.entries = sorted(entries, key=lambda e: e["registry_id"])
        self.requests, self.fail_on_page, self.page_seconds, self.clock = [], fail_on_page, page_seconds, clock

    def transport(self, method, service, path, tenant, body=None):
        assert service == "registry" and method == "GET", (method, service, path)
        url = urlsplit(path)
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        assert url.path == DISCOVERY_PATH and query.get("view") == "discovery_candidates", f"unpaged registry read: {path}"
        self.requests.append((tenant, query))
        if self.fail_on_page is not None and len(self.requests) == self.fail_on_page:
            raise ConnectionError("RemoteDisconnected")
        if self.clock is not None:
            self.clock.now += self.page_seconds
        size = min(int(query["page_size"]), 50)
        after = query.get("page_token")
        rows = [e for e in self.entries if str(e.get("artifact_state")) not in ("retired", "rejected")
                and e["tenant"] == query["tenant_id"] and (after is None or e["registry_id"] > after)]
        page = rows[:size]
        return {"items": [{"entry": {k: e.get(k) for k in ("registry_id", "strategy_id", "artifact_state", "lineage", "metadata")}}
                          for e in page],
                "next_page_token": page[-1]["registry_id"] if len(rows) > size else None}


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def reg_entry(i, *, tenant="t1", state="draft", family="momentum", **meta):
    spec = {"strategy_id": f"spec-{i}", "title": f"TWSE equity {family} {i}", "hypothesis": f"{family} persists",
            "market_scope": {"venues": ["TWSE"], "asset_classes": ["equity"]},
            "execution_profile": {"rebalance_cadence": "swing", "backend_hint": "qlib"},
            "data_dependencies": [{"ref": "ohlcv"}], "governance": {"risk_level": "medium"},
            "provenance": {"source_refs": [f"src-{i}"]}, "evidence_refs": [f"ev-{i}"],
            "metadata": {"strategy_family": family},
            # bulk the scorer never reads
            "signals": [{"name": f"sig-{k}", "expr": "x" * 200} for k in range(5)], "parameters": {"window": 20},
            "description": "d" * 300}
    return {"registry_id": f"reg-{i:05d}", "strategy_id": f"spec-{i}", "artifact_state": state, "tenant": tenant,
            "lineage": {"source_run_ids": [f"run-{i}"], "parent_registry_ids": []},
            "metadata": {"strategy_spec": spec, "tenant_id": tenant, **meta}}


def spec_ports(registry, clock=None, **env):
    from services.persona.pool_autonomy_cycle import HttpPoolCyclePorts
    return HttpPoolCyclePorts(registry.transport, env={k: str(v) for k, v in env.items()}, spec_clock=clock or Clock())


def test_spec_read_is_paged_tenant_scoped_projected_and_never_unpaged():
    registry = FakeRegistry([reg_entry(i) for i in range(120)] + [reg_entry(500, state="retired"),
                            reg_entry(501, tenant="t2")])
    read = spec_ports(registry).read_strategy_specs(PERSONA)
    assert len(read) == 120 and read.pages == 3 and not read.partial and read.stop_reason is None
    assert all(tenant == "t1" and q["tenant_id"] == "t1" and q["page_size"] == "50" for tenant, q in registry.requests)
    assert "page_token" not in registry.requests[0][1] and registry.requests[1][1]["page_token"] == "reg-00049"
    # only the fields the discovery scorer reads travel past the read
    assert all("signals" not in s and "parameters" not in s and "description" not in s for s in read)
    assert read[0]["strategy_spec_registry_id"] == "reg-00000" and read[0]["lineage"]["source_run_ids"] == ["run-0"]
    assert read.summary()["bytes"] > 0 and read.summary()["specs_seen"] == 120


def test_spec_read_stops_at_page_cap_and_resumes_on_the_next_read():
    registry = FakeRegistry([reg_entry(i) for i in range(230)])
    clock = Clock()
    ports = spec_ports(registry, clock, PERSONA_DISCOVERY_SPEC_MAX_PAGES=2, PERSONA_DISCOVERY_SPEC_REUSE_SECONDS=0)
    first = ports.read_strategy_specs(PERSONA)
    assert (len(first), first.pages, first.partial, first.stop_reason) == (100, 2, True, "page_cap")
    second = ports.read_strategy_specs(PERSONA)
    assert second.resumed and second[0]["strategy_spec_registry_id"] == "reg-00100" and second.stop_reason == "page_cap"
    tail = ports.read_strategy_specs(PERSONA)
    assert (len(tail), tail.partial, tail.stop_reason) == (30, True, "resumed_tail")
    full_from_start = ports.read_strategy_specs(PERSONA)
    assert not full_from_start.resumed and full_from_start[0]["strategy_spec_registry_id"] == "reg-00000"
    assert len(registry.requests) == 2 + 2 + 1 + 2


def test_spec_read_stops_at_time_budget():
    clock = Clock()
    registry = FakeRegistry([reg_entry(i) for i in range(500)], page_seconds=4.0, clock=clock)
    read = spec_ports(registry, clock, PERSONA_DISCOVERY_SPEC_BUDGET_SECONDS=10).read_strategy_specs(PERSONA)
    assert (read.pages, len(read), read.partial, read.stop_reason) == (3, 150, True, "time_budget")


def test_failed_first_page_is_unavailable_and_later_page_failure_is_partial_with_counts():
    registry = FakeRegistry([reg_entry(i) for i in range(200)], fail_on_page=1)
    with pytest.raises(RuntimeError, match=r"strategy_spec_read_unavailable:pages=0:specs=0:ConnectionError"):
        spec_ports(registry).read_strategy_specs(PERSONA)
    registry = FakeRegistry([reg_entry(i) for i in range(200)], fail_on_page=3)
    read = spec_ports(registry).read_strategy_specs(PERSONA)
    assert (read.pages, len(read), read.partial, read.stop_reason) == (2, 100, True, "page_failed:ConnectionError")


def test_one_read_serves_every_demand_and_persona_of_the_tenant_within_reuse_window():
    clock = Clock()
    registry = FakeRegistry([reg_entry(i) for i in range(60)] + [reg_entry(900, tenant="t2")])
    ports = spec_ports(registry, clock)
    a = ports.read_strategy_specs(PERSONA)
    assert ports.read_strategy_specs({**PERSONA, "persona_id": "p2"}) is a
    other = ports.read_strategy_specs({"persona_id": "p9", "tenant_id": "t2"})
    assert [s["strategy_spec_registry_id"] for s in other] == ["reg-00900"]
    assert len(registry.requests) == 2 + 1
    clock.now += 601
    ports.read_strategy_specs(PERSONA)
    assert len(registry.requests) == 2 + 1 + 2


def _demand_world(registry, clock, **env):
    from services.persona.pool_autonomy_cycle import HttpPoolCyclePorts, governed_discovery_loader

    class World(HttpPoolCyclePorts):
        def __init__(self):
            super().__init__(self._t, env={k: str(v) for k, v in env.items()}, spec_clock=clock,
                             discovery_loader=governed_discovery_loader(seeds=lambda p: [], route_policy=lambda p: {},
                                                                        capability_snapshot=lambda p: {}))
            self.recorded = []

        def _t(self, method, service, path, tenant, body=None):
            return registry.transport(method, service, path, tenant, body)

        def reconcile_demands(self, persona, members):
            return [{"demand_id": "d1", "status": "open", "tried_families": [], "missing_datasets": []},
                    {"demand_id": "d2", "status": "open", "tried_families": [], "missing_datasets": []}]

        def record_discovery(self, persona, demand_id, record):
            self.recorded.append((demand_id, record))

        def execute_match_action(self, persona, demand, match):
            return {"owner": "research:tickets", "status": "created", "ticket_id": "t"}
    return World()


def test_capped_spec_read_is_recorded_partial_never_no_match_and_failed_read_unavailable():
    clock = Clock()
    # every candidate here is excluded/unfit, so a full read would say "no match"
    registry = FakeRegistry([reg_entry(i, family="carry", negative_memory_match={"warning_level": "blocking"})
                             for i in range(200)])
    world = _demand_world(registry, clock, PERSONA_DISCOVERY_SPEC_MAX_PAGES=2)
    out = PoolAutonomyCycle(world)._discover(PERSONA, {"members": []})
    assert [e["demand_id"] for e in out] == ["d1", "d2"]
    for _, record in world.recorded:
        assert record["partial"] and record["specs_seen"] == 100 and record["spec_pages"] == 2
        assert record["spec_partial_reason"] == "page_cap"
        assert record["reason"] == "partial_spec_read:100_specs_seen_no_match_yet"
        assert record["spec_read"]["max_pages"] == 2 and record["spec_read"]["stop_reason"] == "page_cap"
    assert len(registry.requests) == 2  # both demands share one bounded read
    failing = FakeRegistry([reg_entry(0)], fail_on_page=1)
    world = _demand_world(failing, Clock())
    PoolAutonomyCycle(world)._discover(PERSONA, {"members": []})
    (d1, first), (d2, second) = world.recorded
    assert first["reason"].startswith("discovery_unavailable:RuntimeError: strategy_spec_read_unavailable:pages=0:specs=0")
    assert first["matched"] == 0 and "spec_read" not in first
    # a failed read is not reused: the next demand reads again (one bounded read per demand at most)
    assert second["spec_read"]["specs_seen"] == 1 and len(failing.requests) == 2


def _previous_read_strategy_specs(rows):
    """The unpaged read this task replaced (verbatim logic), kept as the equality reference."""
    from collections.abc import Mapping as _Mapping
    specs = []
    for view in rows or []:
        entry = view.get("entry") if isinstance(view, _Mapping) else None
        if not isinstance(entry, _Mapping) or str(entry.get("artifact_state") or "").lower() in {"retired", "rejected"}:
            continue
        metadata = dict(entry.get("metadata") or {})
        stored = metadata.get("strategy_spec")
        spec = dict(stored) if isinstance(stored, _Mapping) else {}
        for key in ("mechanism", "mechanism_status", "mechanism_support", "negative_memory_match"):
            if key in metadata:
                spec.setdefault(key, metadata[key])
        spec.update({"strategy_id": spec.get("strategy_id") or entry.get("strategy_id"),
                     "strategy_spec_registry_id": entry.get("registry_id"),
                     "lifecycle_state": entry.get("artifact_state"),
                     "metadata": {**(spec.get("metadata") if isinstance(spec.get("metadata"), _Mapping) else {}),
                                  **{k: v for k, v in metadata.items() if k != "strategy_spec"}},
                     "lineage": entry.get("lineage") if isinstance(entry.get("lineage"), _Mapping) else {}})
        specs.append(spec)
    return specs


def _recorded_set():
    rows = [reg_entry(0), reg_entry(1, state="approved"), reg_entry(2, state="candidate", family="carry"),
            reg_entry(3, mechanism={"name": "underreaction", "support": 0.7}, mechanism_status="supported",
                      mechanism_support=0.7),
            reg_entry(4, negative_memory_match={"warning_level": "warning", "matched_ids": ["old-1"]}),
            reg_entry(5, negative_memory_match={"warning_level": "info"}),
            reg_entry(6, state="retired"), reg_entry(7, state="rejected"),
            reg_entry(8, family="value", required_data=["tw-margin-balance"], allowed_use=["research_discovery"]),
            {"registry_id": "reg-00009", "strategy_id": "spec-9", "artifact_state": "draft", "tenant": "t1",
             "lineage": None, "metadata": None},
            {**reg_entry(10), "lineage": {"source_run_ids": [], "parent_registry_ids": ["reg-00001"], "execution_route": "paper"}}]
    rows[1]["metadata"]["strategy_spec"]["mechanism"] = {"name": "spec-level"}
    return rows


@pytest.mark.parametrize("demand", [
    {"demand_id": "d1", "tried_families": [], "missing_datasets": []},
    {"demand_id": "d2", "tried_families": ["carry"], "missing_datasets": []},
    {"demand_id": "d3", "tried_families": [], "missing_datasets": ["tw-margin-balance"]},
])
def test_scoring_is_identical_to_the_previous_unpaged_read(demand):
    import importlib
    module = importlib.import_module("services.control_plane.persona.persona_strategy_discovery")
    rows = _recorded_set()
    registry = FakeRegistry(rows)
    unpaged = [{"entry": {k: e.get(k) for k in ("registry_id", "strategy_id", "artifact_state", "lineage", "metadata")}}
               for e in registry.entries]
    previous = _previous_read_strategy_specs(unpaged)
    current = spec_ports(registry, PERSONA_DISCOVERY_SPEC_PAGE_SIZE=4).read_strategy_specs(PERSONA)
    assert [s["strategy_spec_registry_id"] for s in current] == [s["strategy_spec_registry_id"] for s in previous]
    for old, new in zip(previous, current):
        assert module._candidate_from_strategy_spec(old) == module._candidate_from_strategy_spec(new)
        for key in ("lineage", "mechanism", "mechanism_status", "mechanism_support", "negative_memory_match", "metadata"):
            assert old.get(key) == new.get(key), key
    persona = {"persona_id": "p1", "tenant_id": "t1", "name": "P1", "mandate": "TWSE equity momentum",
               "strategy_family": "momentum", "required_data_sources": ["ohlcv"],
               "metadata": {"market_scope": ["TWSE"], "asset_classes": ["equity"], "data_availability_scope": ["ohlcv"]}}
    kwargs = dict(route_policy={}, capability_snapshot={}, strategy_seeds=[], created_at="2026-11-25T00:00:00Z",
                  discovery_session_id="psd-demand-x", max_matches=50)
    assert (module.discover_for_demand(persona, demand, strategy_specs=current, **kwargs)
            == module.discover_for_demand(persona, demand, strategy_specs=previous, **kwargs))


def test_hosted_scale_three_cycle_reads_stay_far_below_registry_memory_limit(monkeypatch):
    """9,940 hosted-shape registry rows (~5.6 kB) behind the real registry app; three consecutive cycle reads."""
    import json as _json
    import time as _time
    import tracemalloc

    from fastapi.testclient import TestClient

    from services.registry import storage as storage_module
    from services.registry.service import app
    from services.registry.test_discovery_spec_page import _hosted_entries
    from services.registry.test_service import _JWT_SECRET, _jwt
    from services.persona.pool_autonomy_cycle import HttpPoolCyclePorts

    monkeypatch.setenv("REGISTRY_STORE_BACKEND", "memory")
    monkeypatch.setenv("PANTHEON_REGISTRY_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_REGISTRY_JWT_SECRET", _JWT_SECRET)
    monkeypatch.setenv("PANTHEON_REGISTRY_JWT_ISSUER", "")
    monkeypatch.setenv("PANTHEON_REGISTRY_JWT_AUDIENCE", "")
    storage_module.reset_store()
    try:
        with TestClient(app) as client:
            store = storage_module.get_store()
            for e in _hosted_entries():
                store.put(e)
            rows = len(store._entries)
            assert rows >= 9940
            token = _jwt(subject="persona-registry-reader", tenant="tenant-a")
            requests, request_peaks, response_bytes = [], [], []

            def transport(method, service, path, tenant, body=None):
                url = urlsplit(path)
                assert service == "registry" and parse_qs(url.query).get("view") == ["discovery_candidates"], path
                requests.append(path)
                before = tracemalloc.get_traced_memory()[0] if tracemalloc.is_tracing() else 0
                if tracemalloc.is_tracing():
                    tracemalloc.reset_peak()
                resp = client.get(path, headers={"Authorization": f"Bearer {token}", "X-Tenant-Id": tenant})
                assert resp.status_code == 200, resp.text
                if tracemalloc.is_tracing():  # registry-side cost of serving this one page
                    request_peaks.append(tracemalloc.get_traced_memory()[1] - before)
                    response_bytes.append(len(resp.content))
                return resp.json()

            persona = {"persona_id": "p-hosted", "tenant_id": "tenant-a"}
            ports = HttpPoolCyclePorts(transport, env={"PERSONA_DISCOVERY_SPEC_REUSE_SECONDS": "0",
                                                       "PERSONA_DISCOVERY_SPEC_BUDGET_SECONDS": "600"})
            tracemalloc.start()
            base = tracemalloc.get_traced_memory()[0]
            reads, held, started = [], 0, _time.monotonic()
            for _ in range(3):
                read = ports.read_strategy_specs(persona)
                reads.append(read.summary())
                held = max(held, tracemalloc.get_traced_memory()[0] - base)  # what the persona keeps per read
                del read
            elapsed = _time.monotonic() - started
            tracemalloc.stop()
    finally:
        storage_module.reset_store()
    assert [r["pages"] for r in reads] == [40, 40, 40] and all(r["stop_reason"] == "page_cap" for r in reads)
    assert [r["resumed"] for r in reads] == [False, True, True] and all(r["specs_seen"] == 2000 for r in reads)
    assert len(requests) == 120
    assert len(request_peaks) == 120
    assert max(request_peaks) < 8 * 1024 * 1024, max(request_peaks)  # per page served, far below 256 MiB
    assert held < 64 * 1024 * 1024, held  # the reader's cap bounds what the persona holds per tenant
    out = os.environ.get("POOL_CYCLE_SPEC_READ_EVIDENCE_OUT", "").strip()
    if out:
        with open(out, "w") as fh:
            _json.dump({"registry_rows": rows, "reads": reads, "requests": len(requests),
                        "registry_request_python_peak_bytes_max": max(request_peaks),
                        "registry_request_python_peak_bytes_median": sorted(request_peaks)[len(request_peaks) // 2],
                        "response_bytes_max": max(response_bytes), "persona_held_bytes_max": held,
                        "three_reads_seconds": round(elapsed, 3)}, fh, indent=2, sort_keys=True)
