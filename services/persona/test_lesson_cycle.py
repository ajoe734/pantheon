"""LessonCycle against scripted owner transports. The real-owner path is in tests/test_trade_lesson_loop_integration.py."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from services.persona.consult_triggers import RISK_PERSONA_ID
from services.persona.lesson_cycle import MAX_REVIEWS_PER_CYCLE, LessonCycle, lesson_summary
from services.persona.persona_cycle_controller import CaseFileProposal, CaseFileTurnOutput

PERSONA = {"persona_id": "p-alpha", "tenant_id": "tenant-a"}
NOW = datetime(2026, 11, 15, tzinfo=timezone.utc)
ENV = {"PANTHEON_MEMORY_API_URL": "http://memory.test", "PERSONA_JWT_SECRET": "unit-secret",
       "PANTHEON_PERSONA_SERVICE_TOKEN": "persona-token", "PERSONA_API_URL": "http://persona.test"}


@pytest.fixture(autouse=True)
def _service_secret(monkeypatch):
    monkeypatch.setenv("PERSONA_JWT_SECRET", ENV["PERSONA_JWT_SECRET"])


def candidate(n=1, **over):
    return {"lesson_candidate_id": f"lc-{n}", "persona_id": "p-alpha", "tenant_id": "tenant-a", "scope": "execution",
            "proposed_change": "enter on pullbacks", "confidence": 0.7, "review_state": "proposed",
            "created_at": f"2026-11-14T00:00:0{n}Z", "expiry": "2099-01-01T00:00:00Z", "target_env": "paper",
            "trade_episode_ids": ["e1", "e2"], **over}


class Memory:
    def __init__(self, candidates, endorse=(200, {"review_state": "merged"})):
        self.candidates, self.endorse, self.calls = candidates, endorse, []

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url.removeprefix("http://memory.test"), headers, body))
        if method == "GET":
            state = url.split("review_state=")[1]
            return 200, {"candidates": [c for c in self.candidates if c["review_state"] == state]}
        if url.endswith("/risk-review"):
            return 201, {"review_id": "rv-" + url.split("/")[-2], "verdict": body["verdict"]}
        return self.endorse


def risk(verdict="approve", status="completed", decision=None):
    asked = []

    def turn(persona_id, prompt):
        asked.append((persona_id, prompt))
        return SimpleNamespace(status=status, persona_id=persona_id,
                               decision=decision if decision is not None else json.dumps({"verdict": verdict, "reason": "ok"}))
    turn.asked = asked
    return turn


def cycle(memory, turn, **kw):
    return LessonCycle(turn_runner=turn, http=memory, env=ENV, now=lambda: NOW, **kw)


def test_eligible_paper_candidate_is_reviewed_by_the_risk_persona_then_endorsed_with_service_identities():
    memory, turn = Memory([candidate()]), risk()
    result = cycle(memory, turn).govern(PERSONA)["results"][0]
    assert result["status"] == "merged" and result["review_id"] == "rv-lc-1"
    assert [p for p, _ in turn.asked] == [RISK_PERSONA_ID]
    review, endorse = [c for c in memory.calls if c[0] == "POST"]
    assert review[1].endswith("/risk-review") and review[3] == {"verdict": "approve"}
    assert endorse[1].endswith("/paper-endorse") and endorse[3] == {"risk_review": {"review_id": "rv-lc-1"}}
    assert "episodes" not in endorse[3]  # supporting episodes are never supplied by the caller
    import base64
    claims = lambda h: json.loads(base64.urlsafe_b64decode(h["Authorization"].split(".")[1] + "=="))
    assert claims(review[2])["sub"] == RISK_PERSONA_ID and set(claims(review[2])["roles"]) == {"memory-writer", "risk"}
    assert claims(endorse[2])["roles"] == ["persona-svc"] and claims(endorse[2])["tenant_id"] == "tenant-a"


@pytest.mark.parametrize("over,reason", [
    ({"scope": "risk"}, "human_endorsement_required"),
    ({"proposed_change": "raise the position limit"}, "human_endorsement_required"),
    ({"target_env": "canary"}, "human_endorsement_required"),
    ({"expiry": "2000-01-01T00:00:00Z"}, "candidate_expired"),
    ({"trade_episode_ids": []}, "insufficient_supporting_episodes"),
    ({"scope": "pattern", "trade_episode_ids": ["e1", "e2"]}, "insufficient_supporting_episodes"),
    ({"persona_id": "p-other"}, "foreign_candidate"),
])
def test_ineligible_candidates_buy_no_risk_turn_and_touch_nothing(over, reason):
    memory, turn = Memory([candidate(**over)]), risk()
    result = cycle(memory, turn).govern(PERSONA)["results"][0]
    assert result["reason"] == reason and not turn.asked
    assert [c for c in memory.calls if c[0] == "POST"] == []


def test_rejection_is_recorded_and_never_endorsed():
    memory = Memory([candidate()])
    result = cycle(memory, risk("reject")).govern(PERSONA)["results"][0]
    assert result["status"] == "rejected" and all(not c[1].endswith("/paper-endorse") for c in memory.calls)


@pytest.mark.parametrize("turn", [risk(status="error"), risk(decision="approve everything"),
                                  risk(decision=json.dumps({"verdict": "maybe", "reason": "x"}))])
def test_unavailable_or_invalid_risk_answer_changes_nothing(turn):
    memory = Memory([candidate()])
    result = cycle(memory, turn).govern(PERSONA)["results"][0]
    assert result["status"] == "risk_review_unavailable" and [c for c in memory.calls if c[0] == "POST"] == []


def test_owner_refusal_is_recorded_readably():
    memory = Memory([candidate()], endorse=(422, {"detail": {"error": "paper_endorse_failed", "message": "gates failed"}}))
    result = cycle(memory, risk()).govern(PERSONA)["results"][0]
    assert result["status"] == "endorse_refused" and "gates failed" in result["reason"]


def test_review_budget_and_unconfigured_memory():
    memory, turn = Memory([candidate(n) for n in range(1, MAX_REVIEWS_PER_CYCLE + 3)]), risk()
    results = cycle(memory, turn).govern(PERSONA)["results"]
    assert len(turn.asked) == MAX_REVIEWS_PER_CYCLE
    assert [r["status"] for r in results].count("deferred") == 2
    out = LessonCycle(turn_runner=risk(), env={}, http=memory).govern(PERSONA)
    assert out["status"] == "unavailable" and out["reason"] == "memory_owner_unavailable:RuntimeError"


class Registrar:
    configured = True

    def __init__(self):
        self.sent = []

    def register(self, candidates, authorization):
        self.sent.append((candidates, authorization))
        return {"registered": [c["lesson_candidate_id"] for c in candidates[:1]],
                "already_registered": [c["lesson_candidate_id"] for c in candidates[1:]]}


def test_memory_candidates_are_staged_as_proposed_paper_lesson_candidates_only():
    registrar = Registrar()
    output = SimpleNamespace(memory_candidates=[
        {"scope": "pattern", "proposed_change": "wait for the second confirmation", "confidence": 0.6},
        {"scope": "execution", "proposed_change": "avoid the open", "confidence": 1},
        {"summary": "no scope or confidence"}])
    rows = cycle(Memory([]), risk(), registrar=registrar).stage(PERSONA, "packet-1", output)
    assert [r["status"] for r in rows] == ["registered", "already_registered", "not_staged"]
    sent, authorization = registrar.sent[0]
    assert authorization is None  # the registrar mints the tenant-bound service identity
    assert all(c["review_state"] == "proposed" and c["target_env"] == "paper" and c["trade_episode_ids"] == []
               and c["tenant_id"] == "tenant-a" and c["persona_id"] == "p-alpha" for c in sent)
    again = cycle(Memory([]), risk(), registrar=Registrar()).stage(PERSONA, "packet-1", output)
    assert again[0]["lesson_candidate_id"] == rows[0]["lesson_candidate_id"]  # replay keys on the content


def test_stage_without_memory_records_the_reason():
    registrar = Registrar()
    registrar.configured = False
    rows = cycle(Memory([]), risk(), registrar=registrar).stage(
        PERSONA, "p", SimpleNamespace(memory_candidates=[{"scope": "pattern", "proposed_change": "x", "confidence": 0.5}]))
    assert rows[0]["status"] == "not_staged" and rows[0]["reason"].startswith("registration_unavailable")


def _case(source_ids):
    return {"case_file_id": "casefile-1", "groups": {"lessons": {"items": [
        {"item_id": f"lessons:persona:{s}", "source_id": s} for s in source_ids]}}}


def _output(*proposals):
    return CaseFileTurnOutput(orient="o", proposals=list(proposals))


def _proposal(kind="harness_patch", items=(), **parameters):
    return CaseFileProposal(kind=kind, reason="r", evidence_refs=[], parameters=parameters,
                            case_file_item_ids=list(items), none_applicable_reason=None if items else "none")


def test_convert_posts_only_lessons_the_case_file_cites_to_the_owner_route(monkeypatch):
    sent = []

    def http(method, url, headers, body):
        sent.append((method, url, headers, body))
        return 201, {"status": "harness_proposed", "proposal_id": "lrp-1", "kind": "harness_patch"}

    rule = {"kind": "harness_patch", "expected_effect": "fewer weak entries", "spec": {
        "session_id": "s", "base_version": 1, "patch_type": "threshold", "rollback_target": 1,
        "proposed_change": {"cycle_thresholds": {"minimum_evidence_count": 4, "minimum_confidence": 0.9}}}}
    case = _case(["pmem-lesson-lc-1", "pmem-memory-other"])
    out = _output(_proposal(items=["lessons:persona:pmem-lesson-lc-1"], lesson_rule=rule),
                  _proposal(items=["lessons:persona:pmem-memory-other"], lesson_rule=rule),
                  _proposal(items=["lessons:persona:pmem-lesson-lc-1"]),
                  _proposal(kind="no_action"))
    rows = LessonCycle(turn_runner=risk(), http=http, env=ENV).convert(PERSONA, out, {}, case)
    assert [r["status"] for r in rows] == ["harness_proposed", "not_converted"] and rows[0]["lesson_ids"] == ["lc-1"]
    assert rows[1]["reason"] == "lesson_rule_spec_missing"
    method, url, headers, body = sent[0]
    assert url == "http://persona.test/api/personas/p-alpha/lesson-rule-proposals" and method == "POST"
    assert headers == {"Authorization": "Bearer persona-token", "X-Tenant-Id": "tenant-a"}
    assert body["lesson_ids"] == ["lc-1"] and body["case_file"]["case_file_id"] == "casefile-1"


def test_convert_refusal_and_missing_credential_are_recorded():
    rule = {"kind": "gate_check", "expected_effect": "e", "spec": {"gate": "g", "check_name": "c", "description": "d"}}
    out = _output(_proposal(items=["lessons:persona:pmem-lesson-lc-1"], lesson_rule=rule))
    case = _case(["pmem-lesson-lc-1"])
    refused = LessonCycle(turn_runner=risk(), env=ENV, http=lambda *a: (422, {"detail": {"reason": "lesson_not_endorsed:lc-1"}}))
    assert "lesson_not_endorsed:lc-1" in refused.convert(PERSONA, out, {}, case)[0]["reason"]
    bare = LessonCycle(turn_runner=risk(), env={}, http=lambda *a: (_ for _ in ()).throw(AssertionError("no call")))
    assert bare.convert(PERSONA, out, {}, case)[0]["reason"] == "persona_owner_credential_unconfigured"


def test_summary_makes_blocked_steps_visible():
    summary = lesson_summary({"lesson_governance": {"status": "completed", "results": [
        {"status": "merged"}, {"status": "rejected"}, {"status": "endorse_refused"}]},
        "lesson_staging": [{"status": "registered"}], "lesson_rules": [{"proposal_id": "lrp-1"}]})
    assert summary == {"governance": "completed", "governance_reason": None, "merged": 1, "rejected": 1,
                       "blocked": 1, "staged": 1, "rule_proposals": 1}
