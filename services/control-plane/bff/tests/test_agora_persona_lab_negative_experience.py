"""Operator never_again is kept separate from the measured harness observation."""
from __future__ import annotations

from services.persona.test_harness import ROOT, headers, system  # noqa: F401
from services.persona.test_harness_adoption import activate, adoption, observations, observe  # noqa: F401

REASON = "operator rejects this pattern"


def _never_again(a):
    return a.client.post(ROOT + "/patches/patch-1/negative-experience", headers=headers(), json={"reason": REASON})


def test_never_again_after_observed_keeps_both_records(adoption):
    a = adoption
    assert activate(a).status_code == 200
    assert observe(a, observations(a)).json()["experience"]["outcome"] == "helped"
    assert _never_again(a).status_code == 201
    state = a.harness.read("p-alpha")
    row = state["patches"]["patch-1"]
    assert row["experience"]["outcome"] == "helped"
    assert row["operator_feedback"]["outcome"] == "never_again"
    assert row["operator_feedback"]["source_event_id"] != row["experience"]["source_event_id"]
    assert REASON in row["operator_feedback"]["body"]
    assert any(e["action"] == "never_again" for e in state["events"])


def test_never_again_during_experiment_still_finalizes_observation(adoption):
    a = adoption
    assert activate(a).status_code == 200
    assert _never_again(a).status_code == 201
    assert observe(a, observations(a)).status_code == 200
    state = a.harness.read("p-alpha")
    row = state["patches"]["patch-1"]
    assert row["status"] == "observed" and state["active_experiment"] is None
    assert row["experience"]["outcome"] == "helped" and row["operator_feedback"]["outcome"] == "never_again"
    assert state.get("component_cooldowns")


def test_later_proposal_cites_both_observation_and_operator_feedback(adoption):
    from services.persona.harness import PersonaPatch
    a = adoption
    assert activate(a).status_code == 200
    assert observe(a, observations(a)).status_code == 200
    assert _never_again(a).status_code == 201
    state = a.harness.read("p-alpha")
    proposal = dict(state["patches"]["patch-1"]["proposal"], patch_id="patch-2",
                    base_version=state["active_version"], rollback_target=state["active_version"])
    row = a.harness.propose("p-alpha", PersonaPatch.model_validate(proposal), "operator")
    assert sorted(r["outcome"] for r in row["experience_refs"]) == ["helped", "never_again"]
