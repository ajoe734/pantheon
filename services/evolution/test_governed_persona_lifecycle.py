import pytest

from services.evolution.governed_persona_lifecycle import GovernedLifecycleError, GovernedPersonaLifecycle


class FakeWorld:
    """Minimal Governance + persona owner: enforces distinct decider and exact subject."""

    def __init__(self, state="paper_owner", tenant="t1"):
        self.persona = {"tenant_id": tenant, "lifecycle_state": state}
        self.approvals = {}
        self.calls = []

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url.split("/api/", 1)[-1], headers.get("Authorization")))
        path = url.split("/api/", 1)[1]
        if path.startswith("personas/") and method == "GET":
            return 200, dict(self.persona)
        if path.endswith("/lifecycle"):
            a = self.approvals.get(body["governance_decision_id"])
            sub = ((a or {}).get("metadata") or {}).get("subject", {})
            if not a or a["decision_state"] != "decided" or sub.get("from_state") != self.persona["lifecycle_state"] \
                    or sub.get("to_state") != body["target_state"]:
                return 403, {"detail": "approval does not verify"}
            self.persona["lifecycle_state"] = body["target_state"]
            self.persona.setdefault("metadata", {})["last_lifecycle_governance_decision_id"] = body["governance_decision_id"]
            return 200, dict(self.persona)
        if path == "governance/approvals":
            if body["decision_id"] in self.approvals:
                return 409, {"detail": "exists"}
            assert body["risk_level"] == "low" and body["target_type"] == "persona_lifecycle_transition"
            rec = {k: v for k, v in body.items() if k != "subject"}
            self.approvals[body["decision_id"]] = {**rec, "metadata": {"subject": body["subject"]},
                                                   "decision_state": "proposed", "version": 1}
            return 201, self.approvals[body["decision_id"]]
        did = path.split("/")[2]
        a = self.approvals.get(did)
        if a is None:
            return 404, {"detail": "nf"}
        if method == "GET":
            return 200, a
        op = path.rsplit("/", 1)[1]
        assert body["expected_version"] == a["version"]
        if body["actor_id"] == a["owner_user_id"]:
            return 403, {"detail": "decider must not be proposer"}
        if op == "review":
            a.update(decision_state="under_review", version=2)
        else:
            a.update(decision_state="decided", decision=body["outcome"], version=3)
        return 200, a


def _port(world, decision="evo-1"):
    return GovernedPersonaLifecycle(
        governance_url="http://gov", persona_url="http://per",
        proposer_token=lambda: "tok-prop", proposer_id="proposer", gate_token=lambda: "tok-gate", gate_id="gate",
        persona_token=lambda: "tok-per", evolution_decision_id=lambda: decision, transport=world)


def test_each_step_goes_through_governance_then_persona_owner():
    w = FakeWorld()
    p = _port(w)
    assert p.advance("p1", "t1", "frozen", "ignored") == "frozen"
    assert p.advance("p1", "t1", "retired", "ignored") == "retired"
    assert len(w.approvals) == 2
    assert {a["metadata"]["subject"]["to_state"] for a in w.approvals.values()} == {"frozen", "retired"}
    assert [c[2] for c in w.calls if c[0] == "POST"][:3] == ["Bearer tok-prop", "Bearer tok-gate", "Bearer tok-gate"]


def test_replay_reuses_the_decided_approval_and_is_idempotent():
    w = FakeWorld()
    p = _port(w)
    p.advance("p1", "t1", "frozen", "x")
    w.persona["lifecycle_state"] = "paper_owner"  # persona write lost: retry must not re-propose
    assert p.advance("p1", "t1", "frozen", "x") == "frozen"
    assert len(w.approvals) == 1


def test_wrong_tenant_and_same_identity_are_refused():
    with pytest.raises(GovernedLifecycleError):
        _port(FakeWorld(tenant="other")).advance("p1", "t1", "frozen", "x")
    with pytest.raises(ValueError):
        GovernedPersonaLifecycle(governance_url="g", persona_url="p", proposer_token=str, proposer_id="a",
                                 gate_token=str, gate_id="a", persona_token=str, evolution_decision_id=str)


def test_changed_source_between_reads_refuses_to_mint_approval():
    w = FakeWorld()
    w.persona["lifecycle_state"] = "frozen"
    with pytest.raises(GovernedLifecycleError):
        _port(w).advance("p1", "t1", "retired", "x", expected_source="paper_owner")
    assert not w.approvals


# --- production response-model contract -----------------------------------
import sys
from pathlib import Path

_CP_GOV = Path(__file__).resolve().parent.parent / "control-plane" / "governance"
if str(_CP_GOV) not in sys.path:
    sys.path.insert(0, str(_CP_GOV))
from approval_decision import ApprovalDecision  # noqa: E402

from services.governance.models import ApprovalDecisionResponse  # noqa: E402
from services.evolution.governed_persona_lifecycle import transition_approval_id  # noqa: E402


class RealShapeWorld(FakeWorld):
    """Approvals are real ApprovalDecision objects serialized via the production response model."""

    def _real(self, body):
        d = ApprovalDecision.create_proposed(
            decision_id=body["decision_id"], target_type=body["target_type"], target_id=body["target_id"],
            target_version=body["target_version"], risk_level=body["risk_level"], persona_id=body.get("persona_id"),
            tenant_id=body["tenant_id"], owner_user_id=body["owner_user_id"], subject=body.get("subject"),
            expires_at=body.get("expires_at"))
        return d

    def __call__(self, method, url, headers, body):
        path = url.split("/api/", 1)[1]
        if path == "governance/approvals" and method == "POST":
            self.real = getattr(self, "real", {})
            if body["decision_id"] in self.real:
                return 409, {"detail": "exists"}
            self.real[body["decision_id"]] = self._real(body)
            return 201, self._resp(body["decision_id"])
        if path.startswith("governance/approvals/"):
            did = path.split("/")[2]
            d = getattr(self, "real", {}).get(did)
            if d is None:
                return 404, {"detail": "nf"}
            if method == "POST":
                if body["actor_id"] == d.owner_user_id:
                    return 403, {"detail": "decider must not be proposer"}
                if path.endswith("/review"):
                    d.accept_review(body["actor_role"], body["actor_id"])
                else:
                    d.decide(body["outcome"], body["rationale"], actor_role=body["actor_role"],
                             actor_id=body["actor_id"], expires_at=body.get("expires_at"))
            return 200, self._resp(did)
        if path.endswith("/lifecycle"):
            d = self.real.get(body["governance_decision_id"])
            sub = (d.metadata or {}).get("subject", {}) if d else {}
            if not d or getattr(d.decision_state, "value", d.decision_state) != "decided":
                return 403, {"detail": "no"}
            if sub.get("from_state") != self.persona["lifecycle_state"] or sub.get("to_state") != body["target_state"]:
                return 403, {"detail": "no"}
            self.persona["lifecycle_state"] = body["target_state"]
            self.persona.setdefault("metadata", {})["last_lifecycle_governance_decision_id"] = \
                body["governance_decision_id"]
            return 200, dict(self.persona)
        return super().__call__(method, url, headers, body)

    def _resp(self, did):
        d = self.real[did]
        raw = d.to_dict()
        raw.setdefault("event_id", f"evt-{did}")
        raw["event_id"] = raw["event_id"] or f"evt-{did}"
        return ApprovalDecisionResponse(**raw).model_dump(mode="json")


def test_verify_freeze_against_production_response_shape_same_tick_and_resume():
    w = RealShapeWorld()
    p = _port(w)
    assert p.advance("p1", "t1", "frozen", "x") == "frozen"
    assert p.verify_freeze("evo-1", "p1", "t1") is True  # same-tick retirement
    # resume: fresh port instance, persona already frozen by this decision
    assert _port(w).verify_freeze("evo-1", "p1", "t1") is True
    assert p.advance("p1", "t1", "retired", "x") == "retired"


def test_verify_freeze_rejects_external_freeze_and_wrong_tenant_on_production_shape():
    w = RealShapeWorld()
    _port(w, "evo-1").advance("p1", "t1", "frozen", "x")
    assert _port(w, "evo-other").verify_freeze("evo-other", "p1", "t1") is False  # not this decision's freeze
    assert _port(w, "evo-1").verify_freeze("evo-1", "p1", "other-tenant") is False
    w.persona["metadata"]["last_lifecycle_governance_decision_id"] = transition_approval_id(
        "evo-ext", "p1", "paper_owner", "frozen")
    assert _port(w, "evo-1").verify_freeze("evo-1", "p1", "t1") is False
