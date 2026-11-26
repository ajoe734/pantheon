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
                        "strategy_kind": "factor_rule_v1", "persona_lifecycle_state": "paper_owner", "pool_member": {"artifact_ref": "art-1"}}
                       if result == "default" else result)
        self.exc = exc

    def resolve(self, decision):
        if self.exc:
            raise self.exc
        return self.result


class Decisions:
    def __init__(self, items):
        self.items, self.calls = items, []

    def list_by_state(self, state):
        return [i for i in self.items if i.get("decision_state") == state]

    fail_approve = False

    def _item(self, did):
        return next(i for i in self.items if i["decision_id"] == did)

    def review(self, did, body):
        self.calls.append(("review", did, body))
        self._item(did).update(decision_state="reviewed", approval_decision_id=body["approval_decision_id"])
        return {}

    def approve(self, did, body):
        if self.fail_approve:
            raise RuntimeError("decision owner down")
        self.calls.append(("approve", did, body))
        self._item(did).update(decision_state="approved")
        return {}


class Personas:
    def __init__(self, state="paper_owner"):
        self.state, self.log = state, []

    own_freeze = True

    def get_state(self, pid, tenant):
        return self.state

    def verify_freeze(self, did, pid, tenant):
        return self.own_freeze

    before_advance = None  # hook: simulate an external change between reads

    def advance(self, pid, tenant, target, gov, expected_source=None):
        if self.before_advance:
            self.before_advance(self)
        if expected_source is not None and self.state != expected_source:
            raise RuntimeError(f"source changed to {self.state!r}")
        self.log.append((pid, target, gov))
        self.state = target
        return target


class PoolActions:
    def __init__(self):
        self.done = False
        self.down = False

    def retire(self, decision, scope):
        if self.down:
            raise ConnectionError("pool owner down")
        receipt = {"status": "published", "already_removed": self.done, "removed": scope["pool_member"]}
        self.done = True
        return receipt


class ExecApprovals:
    """Stands in for the Governance port: returns the deterministic real-request id, or fails."""

    def __init__(self):
        self.calls, self.fail = [], False

    def ensure_approved(self, decision):
        if self.fail:
            raise RuntimeError("governance down")
        self.calls.append(decision["decision_id"])
        return ac.expected_binding_id(decision)


def _consumer(items, scope=None, personas=None, approvals="default"):
    sink = ac.ListEvidenceSink()
    port = ExecApprovals() if approvals == "default" else approvals
    consumer = ac.AutoConsumer(decisions=Decisions(items), scope=scope or Scope(), evidence=sink,
                               personas=personas, pool_actions=PoolActions(), execution_approvals=port)
    return consumer, sink


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


def test_automatic_retire_removes_member_and_never_touches_persona_lifecycle():
    personas = Personas("paper_owner")
    consumer, sink = _consumer([_decision(action_type="retire", metadata={"discovery_auto_scope": True})],
                                personas=personas)
    report = consumer.tick()
    assert report.approved == report.retired == ["evo-auto-1"]
    assert personas.state == "paper_owner" and not personas.log
    assert any(e["event"] == "pool_member_removed" for e in sink.entries)
    assert not consumer.tick().retired


def _frozen_scope(last_id=""):
    return Scope({"tenant_id": "t1", "persona_id": "per-1", "artifact_id": "art-1", "stage": "frozen",
                  "strategy_kind": "factor_rule_v1", "persona_lifecycle_state": "frozen",
                  "last_lifecycle_governance_decision_id": last_id})


@pytest.mark.parametrize("action", ["retire", "retrain", "mutate", "revalidate"])
def test_fresh_proposal_on_externally_frozen_persona_is_operator_owned(action):
    personas = Personas("frozen")
    consumer, _ = _consumer([_decision(action_type=action, metadata={"discovery_auto_scope": True})],
                            scope=_frozen_scope("approved-live-owner-freeze"), personas=personas)
    report = consumer.tick()
    assert report.left_for_operator == {"evo-auto-1": "artifact_not_paper_only"}
    assert not report.approved and not report.retired and not personas.log


def test_frozen_resume_is_operator_owned_even_with_legacy_own_freeze_proof():
    from services.evolution.governed_persona_lifecycle import transition_approval_id
    own = transition_approval_id("evo-auto-1", "per-1", "paper_owner", "frozen")
    consumer, _ = _consumer([_decision(decision_state="approved", approval_decision_id="auto-gate-evo-auto-1",
        action_type="retire", metadata={"discovery_auto_scope": True})], scope=_frozen_scope(own))
    report = consumer.tick()
    assert not report.retired and report.left_for_operator["evo-auto-1"] == "artifact_not_paper_only"


def test_non_pool_retire_is_left_for_operator():
    scope = Scope()
    scope.result.pop("pool_member")
    consumer, _ = _consumer([_decision(action_type="retire", metadata={"discovery_auto_scope": True})], scope=scope)
    assert consumer.tick().left_for_operator == {"evo-auto-1": "retire_requires_pool_member"}


@pytest.mark.parametrize("override,reason", [
    ({"action_type": "freeze"}, "action_requires_operator"),
    ({"action_type": "reduce_budget"}, "action_requires_operator"),
    ({"action_type": "retire", "risk_level": "high"}, "risk_requires_operator"),  # no marker -> high
    ({"action_type": "retire"}, "retire_outside_discovery_scope"),  # forged low risk, no marker
    ({"risk_level": "medium"}, "risk_requires_operator"),
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
        def list_by_state(self, state):
            rows = client.get("/api/evolution/proposals", params={"state": state}).json()
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
                    "stage": "paper", "strategy_kind": "factor_rule_v1", "pool_member": {"artifact_ref": d["target_id"]}}

    sink = ac.ListEvidenceSink()
    report = ac.AutoConsumer(decisions=Http(), scope=RealScope(), evidence=sink, personas=personas, pool_actions=PoolActions()).tick()
    assert report.approved == ["evo-e2e-retire"], report
    assert report.left_for_operator.get("evo-e2e-plain") == "risk_requires_operator"
    assert personas.state == "paper_owner" and not personas.log
    final = client.get("/api/evolution/proposals/evo-e2e-retire").json()
    assert final["decision_state"] == "approved"
    roles = [s["actor_role"] for s in final["review_chain"]]
    assert roles == ["automated_gate", "automated_gate"]
    assert client.get("/api/evolution/proposals/evo-e2e-plain").json()["decision_state"] == "proposed"


# ------------------------------------------------------------- resumable steps

def test_review_success_then_approve_failure_is_resumed_next_tick():
    consumer, sink = _consumer([_decision()])
    consumer.decisions.fail_approve = True
    first = consumer.tick()
    assert first.errors and consumer.decisions.items[0]["decision_state"] == "reviewed"
    consumer.decisions.fail_approve = False
    second = consumer.tick()
    assert second.approved == ["evo-auto-1"] and consumer.decisions.items[0]["decision_state"] == "approved"
    assert [c[0] for c in consumer.decisions.calls] == ["review", "approve"]  # not reviewed twice
    bound = consumer.decisions.calls[1][2]["approval_decision_id"]
    assert bound == ac.expected_binding_id(consumer.decisions.items[0]) and bound.startswith("apv-evoexec-evo-auto-1-")
    assert bound == consumer.decisions.calls[0][2]["approval_decision_id"]  # same real id both steps


def test_approved_member_removal_retries_owner_outage_with_original_binding():
    consumer, _ = _consumer([_decision(action_type="retire", metadata={ac.DISCOVERY_AUTO_SCOPE_KEY: True})])
    consumer.pool_actions.down = True
    first = consumer.tick()
    assert first.errors and consumer.decisions.items[0]["decision_state"] == "approved"
    consumer.pool_actions.down = False
    assert consumer.tick().retired == ["evo-auto-1"]
    assert not consumer.tick().retired


def test_reviewed_or_approved_decisions_without_the_auto_binding_stay_with_the_operator():
    consumer, _ = _consumer([_decision(decision_state="reviewed", approval_decision_id="human-1")])
    report = consumer.tick()
    assert not report.approved and not consumer.decisions.calls


def test_research_binding_is_the_real_governance_execute_approval_not_a_synthetic_id():
    consumer, sink = _consumer([_decision()])
    consumer.tick()
    review = consumer.decisions.calls[0][2]
    assert review["approval_decision_id"].startswith("apv-evoexec-evo-auto-1-")
    assert not review["approval_decision_id"].startswith("auto-gate-")
    assert sink.entries[0]["approval_decision_id"] == review["approval_decision_id"]


def test_governance_failure_leaves_research_decision_proposed_and_is_retried():
    consumer, _ = _consumer([_decision()])
    consumer.execution_approvals.fail = True
    first = consumer.tick()
    assert first.errors and consumer.decisions.items[0]["decision_state"] == "proposed" and not consumer.decisions.calls
    consumer.execution_approvals.fail = False
    assert consumer.tick().approved == ["evo-auto-1"]


def test_missing_execution_approval_port_never_approves_research_work():
    consumer, _ = _consumer([_decision()], approvals=None)
    report = consumer.tick()
    assert report.errors and not report.approved and not consumer.decisions.calls


def test_reviewed_research_decision_with_synthetic_binding_stays_with_the_operator():
    consumer, _ = _consumer([_decision(decision_state="reviewed", approval_decision_id="auto-gate-evo-auto-1")])
    assert not consumer.tick().approved and not consumer.decisions.calls


def test_automatic_retire_never_uses_a_configured_lifecycle_port():
    personas = Personas("live_owner")
    consumer, _ = _consumer([_decision(action_type="retire", metadata={"discovery_auto_scope": True})], personas=personas)
    consumer.tick()
    assert personas.state == "live_owner" and not personas.log
