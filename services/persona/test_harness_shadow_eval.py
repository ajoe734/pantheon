"""Shadow comparison through the real entry point, real harness owner and a real packet store."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from services.control_plane.ooda.ooda_loop_packet import OodaLoopPacket
from services.persona.cron_ooda_closure import append_ooda_packet
from services.persona.harness import HarnessError
from services.persona.harness_shadow_eval import (
    MAX_COMPARISONS_PER_PATCH, MAX_REPLAY_BUNDLES, DenyAllReplayProvider, run_shadow_comparison,
)
from integrations.openclaw.persona_ooda_runtime import run_persona_ooda_turn
from services.persona.test_harness import (  # noqa: F401  (system is a pytest fixture)
    ROOT, headers, initialize, proposal, receipt, submit, system,
)

BOOK = {"total_return": -0.05, "observation_count": 20, "is_real_order": False}


def seed(path, count, *, unavailable=False, tenant="tenant-a", consult=None):
    for i in range(count):
        packet = OodaLoopPacket(packet_id=f"packet-{i:02d}", loop_type="paper_strategy", status="closed",
                                environment="paper", persona_ids=["p-alpha"],
                                created_at=f"2026-11-0{i % 9 + 1}T00:00:00+00:00",
                                updated_at=f"2026-11-0{i % 9 + 1}T01:00:00+00:00").to_dict()
        packet["tenant_id"] = tenant
        packet["persona_cycle"] = {"phase": "terminal", "failure_reason": None, "observe_bundle": {
            "persona_id": "p-alpha", "tenant_id": tenant, "books": {"m1": BOOK},
            "unavailable": ["own_telemetry"] if unavailable else []}}
        if consult:
            packet["persona_cycle"]["observe_bundle"]["consult_outcomes"] = consult
        append_ooda_packet(path, packet)


REPLAY_AGENT = "p-alpha-shadow-replay"
DENY_ALL_POLICY = {"agent_id": REPLAY_AGENT, "tools": [], "side_effects": "none"}


def wrap(fn, policy=DENY_ALL_POLICY):
    return DenyAllReplayProvider(REPLAY_AGENT, lambda agent_id: policy, lambda agent_id, prompt: fn("p-alpha", prompt))


def turn(proposals):
    output = {"orient": "frozen replay", "proposals": proposals, "memory_candidates": []}
    return SimpleNamespace(status="completed", decision=json.dumps(output), model="stub-model",
                           input_tokens=10, output_tokens=5)


def evidence(n):
    return [f"ref-{i}" for i in range(n)]


def remove(n, confidence):
    return {"kind": "remove", "reason": "weak member", "evidence_refs": evidence(n),
            "parameters": {"member_id": "m1", "confidence": confidence}}


def provider_by_version(base_proposals, candidate_proposals, calls=None):
    def provider(persona_id, prompt):
        if calls is not None:
            calls.append(prompt)
        assert "deny-all" in prompt
        candidate = '"version": "candidate"' in prompt
        return turn(candidate_proposals if candidate else base_proposals)
    return wrap(provider)


def previewed(system, **change):
    initialize(system)
    submit(system, patch_type="threshold",
           proposed_change={"cycle_thresholds": {"minimum_evidence_count": 3, "minimum_confidence": 0.8}},
           **change)
    response = system[0].post(ROOT + "/patches/patch-1/precheck", headers=headers())
    assert response.status_code in (200, 201), response.text
    return system[2]


def test_threshold_candidate_equal_keeps_base(system, tmp_path):
    harness = previewed(system)
    store = tmp_path / "packets.jsonl"
    seed(store, 4)
    same = [remove(3, 0.9)]
    record = run_shadow_comparison(harness, "p-alpha", "patch-1", packet_store_path=store,
                                   provider=provider_by_version(same, same), actor="owner-actor")
    assert record["status"] == "complete" and record["decision"] == "KEEP_BASE"
    assert record["usage"]["model_calls"] == 8 and record["usage"]["input_tokens"] == 80
    assert record["replay"]["side_effects"] == "none"
    stored = harness.read("p-alpha")["patches"]["patch-1"]
    assert stored["status"] == "previewed" and stored["comparisons"][0]["decision"] == "KEEP_BASE"
    assert harness.read("p-alpha")["active_version"] == 1  # comparison never activates


def test_candidate_better_when_gates_and_book_improve(system, tmp_path):
    harness = previewed(system)
    store = tmp_path / "packets.jsonl"
    seed(store, 3)
    # Base proposes a low-confidence removal that fails the gate; candidate proposes a passing one.
    record = run_shadow_comparison(harness, "p-alpha", "patch-1", packet_store_path=store,
                                   provider=provider_by_version([remove(1, 0.2)], [remove(3, 0.9)]),
                                   actor="owner-actor")
    crit = record["criteria"]
    assert crit["candidate_gate_pass_rate"] > crit["base_gate_pass_rate"]
    assert crit["virtual_book_measured"] == 0 or crit["virtual_book_delta_total"] >= 0
    assert record["decision"] == "CANDIDATE_BETTER"


def test_virtual_book_regression_keeps_base(system, tmp_path):
    harness = previewed(system)
    store = tmp_path / "packets.jsonl"
    seed(store, 3)
    reweight = {"kind": "reweight", "reason": "r", "evidence_refs": evidence(3),
                "parameters": {"member_id": "m1", "confidence": 0.9, "weight_delta": 0.5}}
    # Candidate adds a reweight on a member whose frozen book lost money: measurable and negative.
    record = run_shadow_comparison(harness, "p-alpha", "patch-1", packet_store_path=store,
                                   provider=provider_by_version([remove(3, 0.9)], [reweight]),
                                   actor="owner-actor")
    assert record["criteria"]["virtual_book_measured"] == 3
    assert record["criteria"]["virtual_book_delta_total"] < 0
    assert record["decision"] == "KEEP_BASE"


def test_unavailable_provider_is_incomplete_never_pass(system, tmp_path):
    harness = previewed(system)
    store = tmp_path / "packets.jsonl"
    seed(store, 3)

    def down(persona_id, prompt):
        raise ConnectionError("gateway down secret-token")

    for provider in (wrap(down), None):
        record = run_shadow_comparison(harness, "p-alpha", "patch-1", packet_store_path=store,
                                       provider=wrap(provider), actor="owner-actor")
        assert record["status"] == "incomplete" and record["decision"] == "KEEP_BASE"
        assert record["reasons"][0].startswith("provider_unavailable")
        assert "secret-token" not in json.dumps(record)


def test_turn_error_status_is_incomplete(system, tmp_path):
    harness = previewed(system)
    store = tmp_path / "packets.jsonl"
    seed(store, 3)
    bad = lambda pid, prompt: SimpleNamespace(status="error", decision="", model="m")
    record = run_shadow_comparison(harness, "p-alpha", "patch-1", packet_store_path=store,
                                   provider=wrap(bad), actor="owner-actor")
    assert record["status"] == "incomplete" and record["bundles"] == []


@pytest.mark.parametrize("count,unavailable,tenant", [(0, False, "tenant-a"), (2, False, "tenant-a"),
                                                       (5, True, "tenant-a"), (5, False, "tenant-b")])
def test_missing_or_unusable_bundles_fail_closed_with_reason(system, tmp_path, count, unavailable, tenant):
    harness = previewed(system)
    store = tmp_path / "packets.jsonl"
    if count:
        seed(store, count, unavailable=unavailable, tenant=tenant)
    called = []
    record = run_shadow_comparison(harness, "p-alpha", "patch-1", packet_store_path=store,
                                   provider=provider_by_version([remove(3, .9)], [remove(3, .9)], called),
                                   actor="owner-actor")
    assert record["status"] == "incomplete" and "insufficient_frozen_bundles" in record["reasons"]
    assert called == []  # no model usage without data


def test_replays_and_comparisons_are_bounded(system, tmp_path):
    harness = previewed(system)
    store = tmp_path / "packets.jsonl"
    seed(store, 12)
    calls = []
    same = [remove(3, 0.9)]
    record = run_shadow_comparison(harness, "p-alpha", "patch-1", packet_store_path=store, max_replays=500,
                                   provider=provider_by_version(same, same, calls), actor="owner-actor")
    assert len(record["replay"]["bundle_packet_ids"]) == MAX_REPLAY_BUNDLES
    assert len(calls) == 2 * MAX_REPLAY_BUNDLES == record["usage"]["model_calls"]
    for _ in range(MAX_COMPARISONS_PER_PATCH - 1):
        run_shadow_comparison(harness, "p-alpha", "patch-1", packet_store_path=store,
                              provider=provider_by_version(same, same), actor="owner-actor")
    with pytest.raises(HarnessError, match="comparison_budget_exhausted"):
        run_shadow_comparison(harness, "p-alpha", "patch-1", packet_store_path=store,
                              provider=provider_by_version(same, same), actor="owner-actor")


def test_unchecked_patch_is_refused(system, tmp_path):
    initialize(system)
    submit(system)
    store = tmp_path / "packets.jsonl"
    seed(store, 3)
    with pytest.raises(HarnessError, match="precheck_required"):
        run_shadow_comparison(system[2], "p-alpha", "patch-1", packet_store_path=store,
                              provider=provider_by_version([], []), actor="owner-actor")


def test_invalid_model_output_counts_as_failed_not_unavailable(system, tmp_path):
    harness = previewed(system)
    store = tmp_path / "packets.jsonl"
    seed(store, 3)

    def provider(persona_id, prompt):
        if '"version": "candidate"' in prompt:
            return SimpleNamespace(status="completed", decision="not json", model="m")
        return turn([remove(3, 0.9)])

    record = run_shadow_comparison(harness, "p-alpha", "patch-1", packet_store_path=store,
                                   provider=wrap(provider), actor="owner-actor")
    assert record["status"] == "complete" and record["decision"] == "KEEP_BASE"
    assert record["criteria"]["candidate_gate_pass_rate"] == 0


def reweight(delta):
    return {"kind": "reweight", "reason": "r", "evidence_refs": evidence(3),
            "parameters": {"member_id": "m1", "confidence": 0.9, "weight_delta": delta}}


def test_same_kind_different_parameters_and_gate_outcome_is_a_difference(system, tmp_path):
    harness = previewed(system)
    store = tmp_path / "packets.jsonl"
    seed(store, 3)
    # Base reweight -0.5 fails the (stricter) candidate-independent gate via low confidence; candidate +0.5 passes.
    base = [{**reweight(-0.5), "parameters": {"member_id": "m1", "confidence": 0.2, "weight_delta": -0.5}}]
    record = run_shadow_comparison(harness, "p-alpha", "patch-1", packet_store_path=store,
                                   provider=provider_by_version(base, [reweight(0.5)]), actor="owner-actor")
    crit = record["criteria"]
    assert crit["decisions_differing"] == 3 and crit["virtual_book_measured"] == 3
    assert crit["virtual_book_delta_total"] < 0 and record["decision"] == "KEEP_BASE"


def test_rejected_actions_are_noops_not_effects(system, tmp_path):
    harness = previewed(system)
    store = tmp_path / "packets.jsonl"
    seed(store, 3)
    base = [{**remove(3, 0.2)}]
    record = run_shadow_comparison(harness, "p-alpha", "patch-1", packet_store_path=store,
                                   provider=provider_by_version(base, base), actor="owner-actor")
    assert record["criteria"]["decisions_differing"] == 0
    assert all(p["effect"] is None for b in record["bundles"] for p in b["base"]["proposals"])


def admit(n=3):
    return {"kind": "admit", "reason": "r", "evidence_refs": evidence(n),
            "parameters": {"member_id": "m1", "confidence": 0.9}}


def test_required_consult_outcome_missing_is_incomplete_not_better(system, tmp_path):
    harness = previewed(system)
    store = tmp_path / "packets.jsonl"
    seed(store, 3)
    record = run_shadow_comparison(harness, "p-alpha", "patch-1", packet_store_path=store,
                                   provider=provider_by_version([remove(1, 0.2)], [admit()]), actor="owner-actor")
    assert record["status"] == "incomplete" and record["decision"] == "KEEP_BASE"
    assert record["reasons"] == ["consult_outcomes_missing"]


def test_frozen_consult_outcomes_drive_the_comparison(system, tmp_path):
    harness = previewed(system)
    for verdict, expected in (("approve", "CANDIDATE_BETTER"), ("reject", "KEEP_BASE")):
        store = tmp_path / f"{verdict}.jsonl"
        seed(store, 3, consult={"risk_persona": {"verdict": verdict}})
        record = run_shadow_comparison(harness, "p-alpha", "patch-1", packet_store_path=store,
                                       provider=provider_by_version([remove(1, 0.2)], [admit()]),
                                       actor="owner-actor")
        assert record["status"] == "complete" and record["decision"] == expected
        assert {b["candidate"]["proposals"][0]["consult_outcome"] for b in record["bundles"]} == {verdict + "d" if verdict == "approve" else "rejected"}


def test_failed_call_usage_is_recorded(system, tmp_path):
    harness = previewed(system)
    store = tmp_path / "packets.jsonl"
    seed(store, 3)

    def down(persona_id, prompt):
        raise ConnectionError("down")

    record = run_shadow_comparison(harness, "p-alpha", "patch-1", packet_store_path=store,
                                   provider=wrap(down), actor="owner-actor")
    assert record["usage"]["model_calls"] == 1 and record["usage"]["failed_calls"] == 1


@pytest.mark.parametrize("policy", [{**DENY_ALL_POLICY, "tools": ["read"]}, {**DENY_ALL_POLICY, "side_effects": "write"},
                                    {**DENY_ALL_POLICY, "agent_id": "p-alpha"}, {}])
def test_unverified_or_raw_provider_is_never_replayed(system, tmp_path, policy):
    harness = previewed(system)
    store = tmp_path / "packets.jsonl"
    seed(store, 3)
    calls = []
    same = [remove(3, 0.9)]
    raw = provider_by_version(same, same, calls)
    for provider in (wrap(lambda pid, prompt: calls.append(1), policy), lambda pid, prompt: calls.append(1)):
        record = run_shadow_comparison(harness, "p-alpha", "patch-1", packet_store_path=store,
                                       provider=provider, actor="owner-actor")
        assert record["status"] == "incomplete" and record["reasons"] == ["deny_all_provider_unverified"]
        assert record["replay"]["deny_all_verified"] is False
    assert calls == []


def test_real_transport_uses_dedicated_replay_agent(system, tmp_path):
    harness = previewed(system)
    store = tmp_path / "packets.jsonl"
    seed(store, 3)
    bodies = []

    def post(url, body, headers, timeout):
        bodies.append(json.loads(body))
        text = json.dumps({"orient": "x", "proposals": [remove(3, 0.9)], "memory_candidates": []})
        return json.dumps({"output": [{"content": [{"type": "output_text", "text": text}]}]}).encode()

    provider = DenyAllReplayProvider(
        REPLAY_AGENT, lambda agent_id: DENY_ALL_POLICY,
        lambda agent_id, prompt: run_persona_ooda_turn(agent_id, prompt, gateway_url="http://gw", token="t", post=post))
    record = run_shadow_comparison(harness, "p-alpha", "patch-1", packet_store_path=store,
                                   provider=provider, actor="owner-actor")
    assert record["status"] == "complete" and record["replay"]["deny_all_verified"] is True
    assert len(bodies) == 6 and {b["model"] for b in bodies} == {"openclaw/" + REPLAY_AGENT}
