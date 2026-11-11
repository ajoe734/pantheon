"""EVOLUTION-AUTO-CONSUMER-SEARCH: automated gate, governed retire, paper decay."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_tmp = tempfile.mkdtemp(prefix="evo_auto_")
os.environ["EVOLUTION_DATA_DIR"] = _tmp
os.environ["INCIDENT_DATA_DIR"] = _tmp
_CP_GOV = Path(__file__).resolve().parent.parent / "control-plane" / "governance"
if str(_CP_GOV) not in sys.path:
    sys.path.insert(0, str(_CP_GOV))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from services.evolution import auto_consumer as ac  # noqa: E402


# ------------------------------------------------------------------ fakes

def _decision(**kw):
    base = {
        "decision_id": "evo-auto-1", "tenant_id": "t1", "decision_state": "proposed",
        "action_type": "retrain", "risk_level": "low", "created_by_role": "evolution_controller",
        "target_type": "candidate_artifact", "target_id": "art-1", "target_version": "1.0.0",
        "target_stage": "paper", "metadata": {},
    }
    base.update(kw)
    return base


class Scope:
    def __init__(self, result="default", exc=None):
        self.result = ({"tenant_id": "t1", "persona_id": "per-1", "artifact_id": "art-1", "stage": "paper",
                        "strategy_kind": "factor_rule_v1", "persona_lifecycle_state": "paper_owner"}
                       if result == "default" else result)
        self.exc = exc

    def resolve(self, decision):
        if self.exc:
            raise self.exc
        return self.result


class Decisions:
    def __init__(self, items):
        self.items, self.calls = items, []

    def list_proposed(self):
        return self.items

    def review(self, did, body):
        self.calls.append(("review", did, body))
        return {}

    def approve(self, did, body):
        self.calls.append(("approve", did, body))
        return {}


class Personas:
    def __init__(self, state="paper_owner"):
        self.state, self.log = state, []

    def get_state(self, pid, tenant):
        return self.state

    def advance(self, pid, tenant, target, gov):
        self.log.append((pid, target, gov))
        self.state = target
        return target


def _consumer(items, scope=None, personas=None):
    sink = ac.ListEvidenceSink()
    return ac.AutoConsumer(decisions=Decisions(items), scope=scope or Scope(), evidence=sink,
                           personas=personas), sink


# ------------------------------------------------------------------ gate (clause 1)

@pytest.mark.parametrize("action", ["retrain", "mutate", "revalidate"])
def test_discovery_artifact_actions_are_reviewed_and_approved_with_evidence(action):
    consumer, sink = _consumer([_decision(action_type=action)])
    report = consumer.tick()
    assert report.approved == ["evo-auto-1"] and not report.left_for_operator
    calls = consumer.decisions.calls
    assert [c[0] for c in calls] == ["review", "approve"]
    assert all(c[2]["actor_role"] == "automated_gate" for c in calls)
    assert sink.entries[0]["event"] == "auto_gate_approved"
    assert all(c["passed"] for c in sink.entries[0]["checks"])
    assert "discovery_persona_artifact_auto_action" in calls[0][2]["note"]


def test_discovery_paper_retire_is_approved_and_persona_retired_through_governed_path():
    personas = Personas("paper_owner")
    consumer, sink = _consumer(
        [_decision(action_type="retire", metadata={"discovery_auto_scope": True})], personas=personas)
    report = consumer.tick()
    assert report.approved == report.retired == ["evo-auto-1"]
    assert [(t, g) for _p, t, g in personas.log] == [("frozen", "auto-gate-evo-auto-1"), ("retired", "auto-gate-evo-auto-1")]
    retired = [e for e in sink.entries if e["event"] == "persona_retired"][0]
    assert retired["final_state"] == "retired" and retired["transitions"] == ["frozen", "retired"]


def test_retire_is_idempotent_for_already_frozen_or_retired_persona():
    for state, expected in (("frozen", ["retired"]), ("retired", [])):
        personas = Personas(state)
        consumer, _ = _consumer([_decision(action_type="retire", metadata={"discovery_auto_scope": True})], personas=personas)
        consumer.tick()
        assert [t for _p, t, _g in personas.log] == expected


def test_live_persona_is_not_retired_automatically():
    consumer, _ = _consumer([_decision(action_type="retire", metadata={"discovery_auto_scope": True})],
                            personas=Personas("live_owner"))
    report = consumer.tick()
    assert "evo-auto-1" in report.errors and not report.retired


@pytest.mark.parametrize("override,reason", [
    ({"action_type": "freeze"}, "action_requires_operator"),
    ({"action_type": "reduce_budget"}, "action_requires_operator"),
    ({"action_type": "retire", "risk_level": "high"}, "risk_requires_operator"),  # no marker -> high
    ({"action_type": "retire"}, "retire_outside_discovery_scope"),  # forged low risk, no marker
    ({"risk_level": "medium"}, "risk_requires_operator"),
    ({"decision_state": "reviewed"}, "not_proposed"),
    ({"created_by_role": "operator"}, "proposer_not_controller"),
    ({"target_stage": "live"}, "stage_not_paper"),
])
def test_everything_else_stays_with_the_operator(override, reason):
    consumer, sink = _consumer([_decision(**override)])
    report = consumer.tick()
    assert report.left_for_operator == {"evo-auto-1": reason}
    assert not report.approved and not consumer.decisions.calls and not sink.entries


@pytest.mark.parametrize("scope,reason", [
    (None, "not_a_discovery_persona_artifact"),
    ({"tenant_id": "other", "persona_id": "p", "artifact_id": "art-1", "stage": "paper"}, "tenant_mismatch"),
    ({"tenant_id": "t1", "persona_id": "p", "artifact_id": "art-1", "stage": "canary"}, "artifact_not_paper_only"),
    ({"tenant_id": "t1", "persona_id": "p", "artifact_id": "someone-else", "stage": "paper"},
     "target_not_the_resolved_discovery_artifact"),
])
def test_target_must_resolve_to_a_discovery_artifact_through_the_owners(scope, reason):
    consumer, _ = _consumer([_decision()], scope=Scope(scope))
    assert consumer.tick().left_for_operator == {"evo-auto-1": reason}


def test_owner_outage_fails_closed():
    consumer, _ = _consumer([_decision()], scope=Scope(exc=ConnectionError("down")))
    assert consumer.tick().left_for_operator == {"evo-auto-1": "scope_owner_unavailable"}


def test_mutate_requires_factor_rule_artifact():
    scope = Scope({"tenant_id": "t1", "persona_id": "p", "artifact_id": "art-1", "stage": "paper",
                   "strategy_kind": "momentum_v1"})
    consumer, _ = _consumer([_decision(action_type="mutate")], scope=scope)
    assert consumer.tick().left_for_operator == {"evo-auto-1": "mutate_requires_factor_rule_v1"}


def test_one_failing_decision_does_not_stop_the_tick():
    items = [_decision(decision_id="a"), _decision(decision_id="b")]
    consumer, _ = _consumer(items)
    original = consumer.decisions.review

    def flaky(did, body):
        if did == "a":
            raise RuntimeError("boom")
        return original(did, body)

    consumer.decisions.review = flaky
    report = consumer.tick()
    assert report.approved == ["b"] and "a" in report.errors


# ------------------------------------------------------------------ decay (clause 5)

W = lambda s, d: {"sharpe_ratio": s, "max_drawdown": d}


def test_persistent_decay_with_hard_breach_is_decayed():
    v = ac.evaluate_paper_decay(baseline_sharpe=1.0, baseline_max_drawdown=0.10,
                                paper_windows=[W(0.8, 0.1), W(0.6, 0.1), W(0.3, 0.1)])
    assert v.decayed and "sharpe_below_half_of_baseline" in v.reasons and "three_underperforming_windows" in v.reasons


def test_single_bad_window_or_short_history_is_not_decay():
    assert not ac.evaluate_paper_decay(baseline_sharpe=1.0, baseline_max_drawdown=0.1,
                                       paper_windows=[W(1.1, 0.1), W(1.2, 0.1), W(0.2, 0.1)]).decayed
    short = ac.evaluate_paper_decay(baseline_sharpe=1.0, baseline_max_drawdown=0.1, paper_windows=[W(0.0, 0.5)])
    assert not short.decayed and short.reasons == ["insufficient_paper_history"]


def test_retire_proposal_requires_decay():
    ok = ac.evaluate_paper_decay(baseline_sharpe=1.0, baseline_max_drawdown=0.1, paper_windows=[W(1.0, 0.1)] * 3)
    with pytest.raises(ValueError):
        ac.build_retire_proposal(decision_id="d", tenant_id="t", persona_id="p", artifact_id="a",
                                 artifact_version="1.0.0", verdict=ok)


# --------------------------------------- end to end against the real decision service

def test_decayed_strategy_is_retired_end_to_end_through_the_real_evolution_service():
    from services.evolution.main import app

    client = TestClient(app)
    decayed = ac.evaluate_paper_decay(baseline_sharpe=1.0, baseline_max_drawdown=0.10,
                                      paper_windows=[W(0.8, 0.1), W(0.6, 0.1), W(0.3, 0.1)])
    body = ac.build_retire_proposal(decision_id="evo-e2e-retire", tenant_id="default", persona_id="per-e2e",
                                    artifact_id="art-e2e", artifact_version="1.0.0", verdict=decayed)
    resp = client.post("/api/evolution/proposals", json=body)
    assert resp.status_code == 201, resp.text
    assert resp.json()["risk_level"] == "low"

    # A retire WITHOUT the discovery marker keeps its high risk and is left for the operator.
    plain = dict(body, decision_id="evo-e2e-plain", target_id="art-plain", metadata={})
    plain_resp = client.post("/api/evolution/proposals", json=plain)
    assert plain_resp.status_code == 201 and plain_resp.json()["risk_level"] == "high"

    class Http:
        def list_proposed(self):
            rows = client.get("/api/evolution/proposals", params={"state": "proposed"}).json()
            return [r for r in rows if r["decision_id"].startswith("evo-e2e")]

        def review(self, did, b):
            r = client.post(f"/api/evolution/proposals/{did}/review", json=b)
            assert r.status_code == 200, r.text
            return r.json()

        def approve(self, did, b):
            r = client.post(f"/api/evolution/proposals/{did}/approve", json=b)
            assert r.status_code == 200, r.text
            return r.json()

    personas = Personas("paper_owner")

    class RealScope:
        def resolve(self, d):
            return {"tenant_id": d["tenant_id"], "persona_id": "per-e2e", "artifact_id": d["target_id"],
                    "stage": "paper", "strategy_kind": "factor_rule_v1"}

    sink = ac.ListEvidenceSink()
    report = ac.AutoConsumer(decisions=Http(), scope=RealScope(), evidence=sink, personas=personas).tick()
    assert report.approved == ["evo-e2e-retire"], report
    assert report.left_for_operator.get("evo-e2e-plain") == "risk_requires_operator"
    assert personas.state == "retired"
    final = client.get("/api/evolution/proposals/evo-e2e-retire").json()
    assert final["decision_state"] == "approved"
    roles = [s["actor_role"] for s in final["review_chain"]]
    assert roles == ["automated_gate", "automated_gate"]
    assert client.get("/api/evolution/proposals/evo-e2e-plain").json()["decision_state"] == "proposed"
