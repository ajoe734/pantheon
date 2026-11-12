"""Unit tests for the consult policy trigger engine, risk veto and bootstrap."""

from __future__ import annotations

import pytest

from services.persona import consult_triggers as t


def _ids(req):
    return [p.persona_id for p in req.participants]


@pytest.mark.parametrize(
    "decision",
    [t.DECISION_TRIAL_ADMISSION, t.DECISION_ENVELOPE_PUBLICATION, t.DECISION_HARNESS_PATCH],
)
def test_every_gated_decision_requires_risk_persona(decision):
    req = t.required_consultation(decision, "d1", initiator_persona_id="persona-a")
    assert _ids(req) == ["persona-a", t.RISK_PERSONA_ID]
    assert req.risk_persona_id == t.RISK_PERSONA_ID
    assert [p.may_veto for p in req.participants] == [False, True]


@pytest.mark.parametrize("scope", ["guardrails", "consult_policy"])
def test_patch_touching_guardrails_or_consult_policy_adds_red_team(scope):
    req = t.required_consultation(
        t.DECISION_HARNESS_PATCH, "p1", initiator_persona_id="persona-a", touches=[scope, "style"]
    )
    assert _ids(req) == ["persona-a", t.RISK_PERSONA_ID, t.RED_TEAM_PERSONA_ID]


def test_red_team_only_for_patches_and_only_for_those_scopes():
    plain = t.required_consultation(
        t.DECISION_HARNESS_PATCH, "p1", initiator_persona_id="persona-a", touches=["style"]
    )
    assert t.RED_TEAM_PERSONA_ID not in _ids(plain)
    trial = t.required_consultation(
        t.DECISION_TRIAL_ADMISSION, "t1", initiator_persona_id="persona-a", touches=["guardrails"]
    )
    assert t.RED_TEAM_PERSONA_ID not in _ids(trial)


def test_ungated_decision_needs_nothing_and_bad_input_is_refused():
    assert t.required_consultation("note", "x", initiator_persona_id="a") is None
    with pytest.raises(ValueError):
        t.required_consultation(t.DECISION_HARNESS_PATCH, "", initiator_persona_id="a")


def test_risk_persona_initiating_is_not_duplicated():
    req = t.required_consultation(
        t.DECISION_HARNESS_PATCH, "p", initiator_persona_id=t.RISK_PERSONA_ID
    )
    assert _ids(req) == [t.RISK_PERSONA_ID]


PARTICIPANTS = [
    {"persona_id": "a", "role": "primary_reviewer", "may_veto": False},
    {"persona_id": "risk", "role": "risk_reviewer", "may_veto": True},
    {"persona_id": "red", "role": "red_team", "may_veto": False},
]


def test_risk_support_is_adopt_eligible_even_with_disagreement():
    out = t.evaluate_gate(PARTICIPANTS, {"a": "approve", "risk": "approve_with_conditions", "red": "reject"})
    assert out.outcome == t.OUTCOME_ADOPT_ELIGIBLE
    assert out.disagreements  # reported, never voted away
    assert out.agreements == ("support: a, risk",)


@pytest.mark.parametrize("rec", ["reject", "freeze", "rollback"])
def test_risk_oppose_vetoes_even_when_everyone_else_approves(rec):
    out = t.evaluate_gate(PARTICIPANTS, {"a": "approve", "risk": rec, "red": "approve"})
    assert out.outcome == t.OUTCOME_OPERATOR_DECISION
    assert out.operator_decision_required and out.vetoed_by == "risk"


@pytest.mark.parametrize("rec", ["escalate", "request_more_research"])
def test_non_support_from_risk_is_not_auto_adopted(rec):
    out = t.evaluate_gate(PARTICIPANTS, {"a": "approve", "risk": rec, "red": "approve"})
    assert out.outcome == t.OUTCOME_OPERATOR_DECISION and out.vetoed_by is None


def test_missing_required_answer_fails_closed():
    out = t.evaluate_gate(PARTICIPANTS, {"a": "approve", "risk": "approve"})
    assert out.outcome == t.OUTCOME_OPERATOR_DECISION
    assert "red: no independent answer" in out.reasons


class FakePersonaOwner:
    def __init__(self, existing=()):
        self.records = {
            i: {"persona_id": i, "consult_policy_id": t.CONSULT_PANEL_POLICY_ID}
            for i in existing
        }
        self.posts = []

    def __call__(self, method, url, body):
        if method == "GET":
            return list(self.records.values())
        if method == "PATCH":
            self.records[url.rsplit("/", 1)[1]].update(
                {k: v for k, v in body.items() if k != "actor_id"}
            )
            return {}
        self.posts.append(body)
        if body["persona_id"] in self.records:
            return {"_conflict": True}
        self.records[body["persona_id"]] = body
        return body


def test_bootstrap_creates_both_non_trading_personas_once():
    owner = FakePersonaOwner()
    first = t.bootstrap_consult_personas(persona_url="http://p", http=owner, tenant_id="ten")
    assert first == {t.RISK_PERSONA_ID: "created", t.RED_TEAM_PERSONA_ID: "created"}
    for body in owner.posts:
        assert body["lifecycle_state"] == "draft"
        assert body["metadata"]["trading"] is False and body["metadata"]["consultable"] is True
        assert body["tenant_id"] == "ten"
    n = len(owner.posts)
    second = t.bootstrap_consult_personas(persona_url="http://p", http=owner)
    assert set(second.values()) == {"exists"} and len(owner.posts) == n


def test_bootstrap_only_creates_the_missing_one_and_tolerates_a_race():
    owner = FakePersonaOwner(existing=[t.RISK_PERSONA_ID])
    out = t.bootstrap_consult_personas(persona_url="http://p", http=owner)
    assert out == {t.RISK_PERSONA_ID: "exists", t.RED_TEAM_PERSONA_ID: "created"}


def test_risk_persona_initiating_keeps_veto_after_dedup():
    from services.persona import consult_triggers as t

    req = t.required_consultation(
        t.DECISION_HARNESS_PATCH,
        "p-1",
        initiator_persona_id=t.RISK_PERSONA_ID,
        touches=("guardrails",),
    )
    risk = [p for p in req.participants if p.persona_id == t.RISK_PERSONA_ID]
    assert len(risk) == 1 and risk[0].may_veto
    gate = t.evaluate_gate(
        [p.to_dict() for p in req.participants],
        {t.RISK_PERSONA_ID: "reject", t.RED_TEAM_PERSONA_ID: "approve"},
    )
    assert gate.operator_decision_required


def test_bootstrap_sets_consult_policy_and_repairs_half_provisioned_persona():
    owner = FakePersonaOwner()
    owner.records[t.RISK_PERSONA_ID] = {"persona_id": t.RISK_PERSONA_ID, "consult_policy_id": None}
    out = t.bootstrap_consult_personas(persona_url="http://p", http=owner)
    assert out == {t.RISK_PERSONA_ID: "repaired", t.RED_TEAM_PERSONA_ID: "created"}
    assert owner.records[t.RED_TEAM_PERSONA_ID]["consult_policy_id"] == t.CONSULT_PANEL_POLICY_ID
    assert owner.records[t.RISK_PERSONA_ID]["consult_policy_id"] == t.CONSULT_PANEL_POLICY_ID
    again = t.bootstrap_consult_personas(persona_url="http://p", http=owner)
    assert set(again.values()) == {"exists"}
