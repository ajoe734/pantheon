"""Integration: endorsed lessons become harness / gate-check / pool-policy proposals via the real owner route.

Real components: persona owner app (create_app), the harness pipeline, the lesson
lifecycle (TradeLessonCandidateStore + LessonGovernanceService), the case file
digest rules, and the Governance propose-time validation + ApprovalDecision state
machine. Only the HTTP hop to Governance is replaced by an in-process call.
"""
from __future__ import annotations

import copy
import os
import tempfile
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("GOVERNANCE_DATA_DIR", tempfile.mkdtemp(prefix="lrp_gov_"))

from services.governance import main as governance_main  # noqa: E402
from services.governance.models import ProposeApprovalRequest  # noqa: E402
from services.knowledge import case_file as cf  # noqa: E402
from services.persona.harness import PersistentHarnessOwner  # noqa: E402
from services.persona.lesson_governance import (  # noqa: E402
    LessonGovernanceService, TradeLessonCandidateStore, utc_now,
)
from services.persona.lesson_rule_proposals import LessonRuleConverter  # noqa: E402
from services.persona.test_harness import BASE, SECRET, headers  # noqa: E402
from services.persona.write_owner import CreatePersonaRequest, PersistentPersonaOwner, create_app  # noqa: E402
from services.foundation.reliable_delivery import AtomicJsonRecordStore  # noqa: E402

TENANT, PERSONA = "tenant-a", "p-alpha"
ROUTE = f"/api/personas/{PERSONA}/lesson-rule-proposals"
ADOPTION_ROOT = f"/api/personas/{PERSONA}/harness"


class StoreReader:
    """The Memory owner route is a thin wrapper over this store; the lifecycle is the real one."""

    def __init__(self, store):
        self.store = store

    def get(self, lesson_id, tenant_id, authorization):
        return self.store.get(lesson_id)


def _approval_decision_class():
    import importlib.util
    import sys
    path = Path(__file__).resolve().parents[1] / "control-plane" / "governance" / "approval_decision.py"
    spec = importlib.util.spec_from_file_location("approval_decision_lesson_rule_under_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


AD = _approval_decision_class()


class Governance:
    """Runs the real Governance propose-time checks and the real decision state machine."""

    def __init__(self):
        self.created, self.calls = {}, 0

    def __call__(self, payload):
        self.calls += 1
        body = ProposeApprovalRequest.model_validate(payload)
        errors = governance_main.lesson_rule_proposal_errors(body)
        assert not errors, errors
        fields = body.model_dump(mode="json", exclude={"expected_version", "decision_id"})
        decision = AD.ApprovalDecision.create_proposed(decision_id=payload["decision_id"], **fields)
        assert not decision.validate()
        self.created[payload["decision_id"]] = decision
        return decision.to_dict()


def make_lesson(persona=PERSONA, tenant=TENANT, **over):
    base = {"lesson_candidate_id": str(uuid.uuid4()), "reflection_id": str(uuid.uuid4()),
            "trade_episode_ids": ["ep1", "ep2", "ep3"], "persona_id": persona, "tenant_id": tenant,
            "scope": "strategy", "proposed_change": "Cite two independent evidence sources before proposing",
            "confidence": 0.8, "review_state": "proposed", "created_at": utc_now(), "updated_at": utc_now(),
            "expiry": "2099-01-01T00:00:00Z", "reflection_version": "v1"}
    return {**base, **over}


def endorse(service, lesson):
    service.store.create(lesson)
    episodes = [{"trade_episode_id": e, "tenant_id": lesson["tenant_id"], "persona_id": lesson["persona_id"],
                 "regime": r} for e, r in zip(lesson["trade_episode_ids"], ("bull", "bear", "bull"))]
    service.decide(lesson["lesson_candidate_id"], action="endorse", operator_id="op-alice",
                   reason="passes gates", audit_receipt_id=str(uuid.uuid4()), episodes=episodes)


def make_case_file(lesson_ids, persona=PERSONA, tenant=TENANT):
    items = [{"item_id": f"lessons:persona:pmem-lesson-{lid}", "group": "lessons", "kind": "persona",
              "source_owner": "memory", "source_id": f"pmem-lesson-{lid}", "excerpt": "lesson",
              "retrieval_score": 0.9, "retrieval_mode": "hybrid", "subject_fields": {}} for lid in lesson_ids]
    empty = {"status": "empty", "items": [], "truncated": False, "unavailable": []}
    body = {"schema_version": "decision_case_file.v1", "composed_at": "2026-11-13T00:00:00+00:00",
            "subject": {"decision_type": "consult", "persona_id": persona, "tenant_id": tenant,
                        "strategy_family": "trend", "underlyings": [], "instrument_class": "",
                        "member_artifact_refs": [], "backtest_run_refs": [], "regime_tag": ""},
            "caller": {"actor_id": "owner-actor", "is_subject_persona": True},
            "lesson_query": {"query": "q", "tags": []},
            "budgets": {"max_items_per_group": 8, "max_excerpt_chars": 400, "max_total_chars": 12000},
            "groups": {"facts": copy.deepcopy(empty), "similar_history": copy.deepcopy(empty),
                       "lessons": {"status": "ok", "items": items, "truncated": False, "unavailable": []},
                       "evidence": copy.deepcopy(empty)},
            "execution_may_read": False}
    digest = cf._digest(body)
    return {"case_file_id": f"casefile-{digest[:24]}", "digest": digest, **body}


@pytest.fixture
def system(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONA_AUTH_MODE", "strict")
    monkeypatch.setenv("PERSONA_JWT_SECRET", SECRET)
    owner = PersistentPersonaOwner.from_json_path(tmp_path / "personas.json")
    owner.create(CreatePersonaRequest(actor_id="owner-actor", persona_id=PERSONA, name="Alpha",
                                     mandate="Paper analysis", tenant_id=TENANT))
    raw = owner._records.get(PERSONA)
    assert owner._records.compare_and_set(PERSONA, raw, {**raw, "lifecycle_state": "paper_owner"})[0]
    harness = PersistentHarnessOwner(owner)
    lessons = LessonGovernanceService(TradeLessonCandidateStore(path=tmp_path / "lessons.json"))
    governance = Governance()
    converter = LessonRuleConverter(AtomicJsonRecordStore(tmp_path / "lrp.json"), harness,
                                    reader=StoreReader(lessons.store), governance_sender=governance)
    client = TestClient(create_app(owner=owner, harness_owner=harness, lesson_rule_converter=converter))
    assert client.post(ADOPTION_ROOT, headers=headers(), json=BASE).status_code == 201
    return client, harness, lessons, governance, converter


def request(kind, lesson_ids, **spec):
    body = {"kind": kind, "lesson_ids": lesson_ids, "case_file": make_case_file(lesson_ids),
            "expected_effect": "Avoid repeating the endorsed lesson"}
    body.update(spec)
    return body


HARNESS_SPEC = {"harness_patch": {
    "session_id": "session-1", "base_version": 1, "patch_type": "threshold",
    "proposed_change": {"cycle_thresholds": {"minimum_evidence_count": 4, "minimum_confidence": 0.85}},
    "rollback_target": 1}}
GATE_SPEC = {"gate_check": {"gate": "research_validation", "check_name": "second_regime_holdout",
                           "description": "Require a held-out regime before promotion",
                           "parameters": {"min_regimes": 2}}}
POOL_SPEC = {"pool_policy": {"pool_id": "pool-1", "parameter": "min_member_age_days",
                            "proposed_value": 30, "current_value": 14}}


def test_each_proposal_type_is_created_through_the_real_route(system):
    client, harness, lessons, governance, _ = system
    ids = []
    for _ in range(3):
        lesson = make_lesson()
        endorse(lessons, lesson)
        ids.append(lesson["lesson_candidate_id"])
    case_file_ids = {}

    # harness patch: existing pipeline, paper rules
    r = client.post(ROUTE, headers=headers(), json=request("harness_patch", [ids[0]], **HARNESS_SPEC))
    assert r.status_code == 201, r.text
    row = r.json()
    assert row["status"] == "harness_proposed" and row["lesson_ids"] == [ids[0]]
    assert row["case_file_id"].startswith("casefile-") and row["changes_execution_or_limits"] is False
    patch = harness.read(PERSONA)["patches"][row["patch_id"]]
    assert patch["status"] == "draft"  # not active: precheck/shadow/adoption still required
    assert patch["proposal"]["evidence_refs"][0] == {"kind": "lesson", "ref": ids[0], "moment": row["case_file_id"]}
    assert harness.read(PERSONA)["active_version"] == 1
    checked = client.post(f"{ADOPTION_ROOT}/patches/{row['patch_id']}/precheck", headers=headers())
    assert checked.status_code == 200, checked.text
    assert "trajectory_evidence_unresolved" not in checked.text  # lesson evidence resolves via the owner record

    # gate check: governance decision pending
    r = client.post(ROUTE, headers=headers(), json=request("gate_check", [ids[1]], **GATE_SPEC))
    assert r.status_code == 201, r.text
    gate = r.json()
    assert gate["status"] == "pending_governance_decision"
    decision = governance.created[gate["governance_decision_id"]]
    assert decision.target_type == "lesson_gate_check_proposal" and str(decision.decision_state.value) == "proposed"
    assert decision.decision is None and decision.risk_level in ("high", AD.RiskLevel.HIGH)
    assert decision.metadata["subject"]["lesson_ids"] == ids[1]
    assert decision.metadata["subject"]["case_file_id"] == gate["case_file_id"]
    with pytest.raises(ValueError):  # an automated gate cannot decide it
        decision.accept_review("automated_gate", "gate-bot")

    # pool policy: governance decision pending
    r = client.post(ROUTE, headers=headers(), json=request("pool_policy", [ids[2]], **POOL_SPEC))
    assert r.status_code == 201, r.text
    pool = r.json()
    decision = governance.created[pool["governance_decision_id"]]
    assert decision.target_type == "pool_policy_parameter_proposal" and str(decision.decision_state.value) == "proposed"
    assert decision.target_id == "pool-1"

    listed = client.get(ROUTE, headers=headers()).json()["proposals"]
    assert {p["kind"] for p in listed} == {"harness_patch", "gate_check", "pool_policy"}

    # replay is idempotent: no second decision, no second patch
    again = client.post(ROUTE, headers=headers(), json=request("gate_check", [ids[1]], **GATE_SPEC))
    assert again.json()["proposal_id"] == gate["proposal_id"] and governance.calls == 2


@pytest.mark.parametrize("state", ["proposed", "pending_review", "rejected", "quarantined", "expired"])
def test_unendorsed_lessons_cannot_be_converted(system, state):
    client, _, lessons, governance, _ = system
    lesson = make_lesson()
    lessons.store.create(lesson)
    if state != "proposed":
        lessons.submit_review(lesson["lesson_candidate_id"])
    if state in ("rejected", "quarantined", "expired"):
        lessons.decide(lesson["lesson_candidate_id"], action={"rejected": "reject", "quarantined": "quarantine",
                                                              "expired": "expire"}[state],
                       operator_id="op", reason="no", audit_receipt_id=str(uuid.uuid4()))
    r = client.post(ROUTE, headers=headers(), json=request("gate_check", [lesson["lesson_candidate_id"]], **GATE_SPEC))
    assert r.status_code == 422 and "lesson_not_endorsed" in r.text
    assert governance.calls == 0 and client.get(ROUTE, headers=headers()).json()["proposals"] == []


def test_lesson_must_be_cited_in_a_valid_case_file_of_the_same_persona(system):
    client, _, lessons, governance, _ = system
    lesson, other = make_lesson(), make_lesson(persona="p-other")
    endorse(lessons, lesson)
    endorse(lessons, other)
    lid = lesson["lesson_candidate_id"]
    body = request("gate_check", [lid], **GATE_SPEC)
    tampered = {**body, "case_file": {**body["case_file"], "subject": {**body["case_file"]["subject"], "strategy_family": "x"}}}
    assert "case_file_digest_mismatch" in client.post(ROUTE, headers=headers(), json=tampered).text
    uncited = {**body, "case_file": make_case_file([str(uuid.uuid4())])}
    assert "lesson_not_in_case_file" in client.post(ROUTE, headers=headers(), json=uncited).text
    foreign = request("gate_check", [other["lesson_candidate_id"]], **GATE_SPEC)
    assert "lesson_belongs_to_another_persona" in client.post(ROUTE, headers=headers(), json=foreign).text
    assert "lesson_not_found" in client.post(ROUTE, headers=headers(), json=request(
        "gate_check", [str(uuid.uuid4())], **GATE_SPEC)).text
    assert governance.calls == 0


def test_conversion_never_touches_execution_risk_or_capital(system):
    client, harness, lessons, governance, _ = system
    lesson = make_lesson()
    endorse(lessons, lesson)
    lid = lesson["lesson_candidate_id"]
    for param in ("max_capital_per_member", "risk_budget", "position_limit", "execution_mode"):
        spec = {"pool_policy": {**POOL_SPEC["pool_policy"], "parameter": param}}
        r = client.post(ROUTE, headers=headers(), json=request("pool_policy", [lid], **spec))
        assert r.status_code == 422 and "touches_execution_risk_or_capital" in r.text
    # harness patches are limited to thresholds / consult policy; an execution surface is refused by the model
    bad = {"harness_patch": {**HARNESS_SPEC["harness_patch"], "patch_type": "tool_profile"}}
    assert client.post(ROUTE, headers=headers(), json=request("harness_patch", [lid], **bad)).status_code == 422
    # loosening a threshold is refused by the existing harness precheck, not bypassed
    loosen = {"harness_patch": {**HARNESS_SPEC["harness_patch"], "proposed_change": {
        "cycle_thresholds": {"minimum_evidence_count": 1, "minimum_confidence": 0.1}}}}
    row = client.post(ROUTE, headers=headers(), json=request("harness_patch", [lid], **loosen)).json()
    result = client.post(f"{ADOPTION_ROOT}/patches/{row['patch_id']}/precheck", headers=headers())
    assert "lowered_cycle_threshold" in result.text
    assert governance.calls == 0 and harness.read(PERSONA)["active_version"] == 1


def test_governance_refuses_malformed_or_low_risk_lesson_proposals():
    base = {"expected_version": 0, "tenant_id": TENANT, "owner_user_id": "persona-svc",
            "target_type": "lesson_gate_check_proposal", "target_id": "g", "target_version": "v",
            "risk_level": "high", "persona_id": PERSONA,
            "subject": {"proposal_kind": "gate_check", "lesson_ids": "a", "case_file_id": "c", "proposal_digest": "d"}}
    ok = ProposeApprovalRequest.model_validate(base)
    assert governance_main.lesson_rule_proposal_errors(ok) == []
    low = ProposeApprovalRequest.model_validate({**base, "risk_level": "low"})
    assert any("risk_level" in e for e in governance_main.lesson_rule_proposal_errors(low))
    missing = ProposeApprovalRequest.model_validate({**base, "subject": {"proposal_kind": "gate_check"}})
    assert any("lesson_ids" in e for e in governance_main.lesson_rule_proposal_errors(missing))
    wrong = ProposeApprovalRequest.model_validate({**base, "subject": {**base["subject"], "proposal_kind": "pool_policy"}})
    assert any("proposal_kind" in e for e in governance_main.lesson_rule_proposal_errors(wrong))


def test_governance_unavailable_is_recorded_and_retry_succeeds(system):
    client, _, lessons, governance, converter = system
    lesson = make_lesson()
    endorse(lessons, lesson)
    from services.persona.lesson_rule_proposals import LessonRuleError

    def down(payload):
        raise LessonRuleError("governance_unavailable", 503)
    converter.send = down
    body = request("pool_policy", [lesson["lesson_candidate_id"]], **POOL_SPEC)
    r = client.post(ROUTE, headers=headers(), json=body)
    assert r.status_code == 503 and "governance_unavailable" in r.text
    assert client.get(ROUTE, headers=headers()).json()["proposals"][0]["status"] == "governance_delivery_pending"
    converter.send = governance
    done = client.post(ROUTE, headers=headers(), json=body)
    assert done.status_code == 201 and done.json()["status"] == "pending_governance_decision"


def test_other_tenant_or_missing_auth_is_refused(system):
    client, _, lessons, _, _ = system
    lesson = make_lesson()
    endorse(lessons, lesson)
    body = request("gate_check", [lesson["lesson_candidate_id"]], **GATE_SPEC)
    assert client.post(ROUTE, json=body, headers={"X-Tenant-Id": TENANT}).status_code in (401, 403)
    assert client.post(ROUTE, json=body, headers=headers(tenant="tenant-b")).status_code in (403, 404)


def test_production_reader_passes_real_memory_read_route_for_persona_owner(tmp_path, monkeypatch):
    """Regression: persona.admin caller JWT must be admitted by the real Memory GET, tenant-bound."""
    import io
    import json as _json
    from urllib.error import HTTPError
    from services.memory import main as memory_main
    from services.persona.lesson_rule_proposals import MemoryLessonReader, LessonRuleError

    monkeypatch.setenv("PANTHEON_TRADE_LESSON_CANDIDATE_STORE", str(tmp_path / "mem_lessons.json"))
    monkeypatch.setenv("PANTHEON_PERSONA_MEMORY_STORE", str(tmp_path / "pm.json"))
    monkeypatch.setenv("PANTHEON_MEMORY_STORE", str(tmp_path / "im.json"))
    monkeypatch.setenv("PANTHEON_MEMORY_AUTHZ_MODE", "local")
    monkeypatch.setenv("PANTHEON_MEMORY_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_MEMORY_JWT_SECRET", SECRET)
    for fn in (memory_main._candidate_store, memory_main._persona_store, memory_main._store):
        if hasattr(fn, "cache_clear"):
            fn.cache_clear()
    lesson = make_lesson()
    memory_main._candidate_store().create(lesson)
    mem = TestClient(memory_main.app)

    def bridge(request, timeout=None):
        url = request.full_url.split("://", 1)[1]
        path = url[url.index("/"):]
        resp = mem.get(path, headers={k: v for k, v in request.header_items()})
        if resp.status_code != 200:
            raise HTTPError(request.full_url, resp.status_code, "err", {}, io.BytesIO(resp.content))
        return io.BytesIO(resp.content)

    reader = MemoryLessonReader("http://memory.test", urlopen_fn=bridge)
    auth = headers()["Authorization"]
    got = reader.get(lesson["lesson_candidate_id"], TENANT, auth)
    assert got["lesson_candidate_id"] == lesson["lesson_candidate_id"]
    # tenant boundary preserved: another tenant's persona.admin cannot read it
    other = headers("tenant-b")["Authorization"]
    assert reader.get(lesson["lesson_candidate_id"], "tenant-b", other) is None
    # persona.admin is not admitted to list/other read routes
    assert mem.get("/api/memory/trade-lessons", headers={"Authorization": auth}).status_code == 403
