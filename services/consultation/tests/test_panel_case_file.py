"""Case-file composition and citation rejection through the real panel entry."""
from __future__ import annotations

import hashlib
import json
from dataclasses import replace

import pytest

from services.consultation import workflow_executor as wf
from services.consultation.client import ConsultationServiceClient
from services.consultation.provider import QualifiedContribution
from services.consultation.tests.test_persona_panel_integration import (
    INITIATOR, SERVICE_TOKEN, TENANT, _bootstrap, _config, _get,
    _provider, _provider_fake, stack, triggers,
)
from services.consultation.workflow_state import WorkflowStateStore

# Capture all adapter-local modules together so root pytest's service isolation
# restores the same provider/provisioner pair when this module runs.
import sys
from pathlib import Path
_ADAPTER = Path(__file__).resolve().parents[2] / "openclaw-gateway-adapter"
sys.path.insert(0, str(_ADAPTER))
import consult_agent_provisioning as provisioning
from consultation_provider import create_app, consult_agent_id, CONSULTATION_CONTRIBUTION_PATH
from main import _OPENCLAW_AGENT_WORKSPACE_ROOT, _PERSONA_OPINION_RUNTIME_POLICY


@pytest.mark.parametrize("canary", [False, True], ids=["trial-admission", "canary-candidacy"])
def test_panel_composes_case_and_rejects_uncited_answer(stack, canary):
    _bootstrap(stack)
    red = triggers.RED_TEAM_PERSONA_ID
    risk = triggers.RISK_PERSONA_ID
    # Canary's target is a persona, NOT a member registry id. No case fields
    # are added by the caller (same shape as persona-evaluator candidacy_panel).
    target = INITIATOR if canary else "reg-trial"
    requirement = triggers.required_consultation(
        "trial_admission", target, initiator_persona_id=INITIATOR,
        touches=("paper_canary_candidacy",) if canary else (),
        target_version="ranking-snapshot-7" if canary else "1")
    answers = {p.persona_id: "approve" for p in requirement.participants}
    invalid_persona = red if canary else risk
    with _provider_fake(answers, uncited=(invalid_persona,)) as fake:
        config = _config(stack, f"http://127.0.0.1:{fake.server_address[1]}/contribute")
        created = ConsultationServiceClient(
            base_url=stack["api"], token=SERVICE_TOKEN, tenant_id=TENANT
        ).open_persona_panel(requirement, persona_id=INITIATOR, trace_id="trace-case")
        state = WorkflowStateStore(config.state_path)
        result = wf.run_tick(config=config, state=state, provider=_provider(config))
    assert result["completed"] == 1, result
    cases = [c["payload"]["request"]["case_file"] for c in fake.calls]
    assert len(cases) == len(answers) and all(c == cases[0] for c in cases)
    case = cases[0]
    assert case["subject"]["member_artifact_refs"] == (["reg-member"] if canary else [target])
    assert fake.calls[0]["payload"]["request"]["case_file_binding"]["gated_decision"] == requirement.to_panel_metadata()["gated_decision"]
    import jsonschema
    from pathlib import Path
    jsonschema.validate(case, json.loads((Path(wf.decision_case.__file__).with_name("case_file.schema.json")).read_text()))
    assert "momentum" in case["lesson_query"]["query"]
    assert target in case["lesson_query"]["query"]
    if canary:
        assert "paper_canary_candidacy" in case["lesson_query"]["query"]
    body = {k: v for k, v in case.items() if k not in {"case_file_id", "digest"}}
    assert case["digest"] == hashlib.sha256(json.dumps(
        body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    rid = created["request_id"]
    transcript = _get(stack["api"], f"/api/consult/requests/{rid}/transcript")
    attached = next(e for e in transcript["events"] if e["event_type"] == "panel_case_file_attached")
    assert attached["content"]["case_file"] == case
    rejected = [e for e in transcript["events"] if e["event_type"] == "panel_contribution_rejected"]
    assert [e["content"]["reason"] for e in rejected] == ["claim_uncited"]
    synthesis = next(e["content"] for e in transcript["events"] if e["event_type"] == "panel_synthesis")
    assert synthesis["gate"]["operator_decision_required"] is True
    assert synthesis["rejected_contributions"][0]["persona_id"] == invalid_persona
    accepted = [e for e in transcript["events"] if e["event_type"] == "persona_contribution"]
    assert {e["actor"]["actor_id"] for e in accepted} == set(answers) - {invalid_persona}
    assert all(e["content"]["claims"][0]["evidence_refs"] for e in accepted)
    memo = _get(stack["api"], f"/api/consult/memos?request_id={rid}")[0]
    assert case["case_file_id"] in memo["summary"] and case["digest"] in memo["summary"]
    assert f"{invalid_persona} (" not in memo["summary"]
    assert "Invalid contributions excluded" in memo["summary"]


def _contribution():
    return QualifiedContribution.from_payload({
        "contribution_id": "c1", "tenant_id": "t1", "request_id": "r1",
        "participant_type": "persona", "participant_ref": "p1", "summary": "summary",
        "recommendation": "approve", "confidence": .7,
        "event_content": {"claims": [{"claim": "bounded claim", "evidence_refs": ["item-1"]}],
                          "summary_evidence_refs": ["item-1"]},
        "evidence": [{"id": "item-1", "evidence_type": "case_file_item", "link": "casefile://1"}],
        "findings": []}, expected_tenant_id="t1", expected_request_id="r1")


CASE = {"subject": {"tenant_id": "t1"}, "groups": {"facts": {
    "items": [{"item_id": "item-1", "subject_fields": {"strategy_id": "momentum"}}]}}}


@pytest.mark.parametrize("change,reason", [
    ({"claims": []}, "claims_missing"),
    ({"claims": [{"claim": "uncited"}]}, "claim_uncited"),
    ({"claims": [{"claim": "forged", "evidence_refs": ["other-file-item"]}]}, "case_item_unknown"),
    ({"summary_evidence_refs": []}, "claim_uncited"),
    ({"claims": [{"claim": "wrong subject", "evidence_refs": ["item-1"],
                  "subject_fields": {"strategy_id": "carry"}}]}, "claim_subject_mismatch"),
    ({"claims": [{"claim": "bad subject", "evidence_refs": ["item-1"],
                  "subject_fields": ["momentum"]}]}, "claim_subject_malformed"),
    ({"other_claim": "smuggled assertion"}, "unvalidated_claim_fields"),
])
def test_per_claim_citations_cannot_be_replaced_by_global_evidence(change, reason):
    contribution = _contribution()
    assert wf._case_file_rejection(contribution, CASE) is None
    changed = replace(contribution, event_content={**contribution.event_content, **change})
    assert wf._case_file_rejection(changed, CASE) == reason


def test_uncited_finding_is_invalid():
    contribution = replace(_contribution(), findings=({"claim": "finding", "evidence_refs": []},))
    assert wf._case_file_rejection(contribution, CASE) == "claim_uncited"


def test_all_invalid_participants_leave_operator_hold():
    gate, summary, recommendation, findings, confidence = wf._synthesize(
        [{"persona_id": "risk", "role": "risk_reviewer", "may_veto": True}], [])
    assert gate["operator_decision_required"] and recommendation == "escalate"
    assert confidence == 0


def test_missing_owner_records_reason_and_calls_no_agent(stack, monkeypatch):
    monkeypatch.delenv("PANTHEON_REGISTRY_API_URL")
    requirement = triggers.required_consultation("trial_admission", "reg-x", initiator_persona_id=INITIATOR)
    with _provider_fake({INITIATOR: "approve", triggers.RISK_PERSONA_ID: "approve"}) as fake:
        config = _config(stack, f"http://127.0.0.1:{fake.server_address[1]}/contribute")
        created = ConsultationServiceClient(base_url=stack["api"], token=SERVICE_TOKEN, tenant_id=TENANT).open_persona_panel(
            requirement, persona_id=INITIATOR, trace_id="trace-missing")
        result = wf.run_tick(config=config, state=WorkflowStateStore(config.state_path), provider=_provider(config))
    assert result["completed"] == 0 and not fake.calls
    transcript = _get(stack["api"], f"/api/consult/requests/{created['request_id']}/transcript")
    failure = next(e for e in transcript["events"] if e["event_type"] == "panel_case_file_unavailable")
    assert "owner_unconfigured" in failure["content"]["reason"]


@pytest.mark.parametrize("canary,memory_available", [(False, True), (True, True), (True, False)])
def test_real_adapter_receives_case_and_queries_each_personas_own_memory(stack, canary, memory_available, monkeypatch):
    """Case-file routing with preprovisioned agent/owner-read boundary doubles."""
    from types import SimpleNamespace
    from services.consultation.tests.test_persona_panel_integration import _serve, PROVIDER_TOKEN

    requirement = triggers.required_consultation(
        "trial_admission", INITIATOR if canary else "reg-trial", initiator_persona_id=INITIATOR,
        touches=("paper_canary_candidacy",) if canary else ())
    personas = [p.persona_id for p in requirement.participants]
    queries, prompts = [], []

    def memory(tenant, persona, session, query):
        queries.append((tenant, persona, query))
        if not memory_available:
            return None
        def hit(pid, scope, text):
            return {"type": "persona", "relevance_score": 1., "entry": {
                "memory_id": "memory-" + text, "persona_id": pid, "tenant_id": tenant,
                "relevance_scope": scope, "memory_type": "lesson", "content": {"summary": text}}}
        return {"hits": [hit(persona, "persona_and_committee", "SHARED-" + persona),
                         hit(persona, "persona_private", "PRIVATE-SENTINEL"),
                         hit("persona-stranger", "persona_and_committee", "OTHER-PERSONA-SENTINEL")]}

    monkeypatch.setenv("PANTHEON_OPENCLAW_AGENT_REGISTRY_LOCK", str(stack["root"] / "agents.lock"))
    monkeypatch.setattr(provisioning, "_persona_record", lambda tenant, persona: {"tenant_id": tenant})

    class Gateway:
        def _gateway_call(self, method, **kwargs):
            assert method == "config.get"  # already provisioned: ensure must be a no-op
            return {"valid": True, "hash": "ready", "config": {"agents": {"list": [
                {"id": "main", "default": True}, *[
                    {"id": consult_agent_id(TENANT, p), **_PERSONA_OPINION_RUNTIME_POLICY,
                     "workspace": str(_OPENCLAW_AGENT_WORKSPACE_ROOT / consult_agent_id(TENANT, p))}
                    for p in personas]]}}}

        def gateway_agents_list(self, **kwargs):
            return [{"id": consult_agent_id(TENANT, p)} for p in personas]

        def invoke(self, prompt, **kwargs):
            prompts.append(prompt)
            envelope = json.loads(prompt.split("Consultation envelope JSON: ", 1)[1])
            request = envelope["request"]
            persona = request["participant"]["persona_id"]
            assert "SHARED-" + persona in prompt
            assert "PRIVATE-SENTINEL" not in prompt and "OTHER-PERSONA-SENTINEL" not in prompt
            assert all("SHARED-" + p not in prompt for p in personas if p != persona)
            assert "case_file_id" in prompt and "summary_evidence_refs" in prompt
            case = request["case_file"]
            item = case["groups"]["facts"]["items"][0]["item_id"]
            refs = [] if persona == INITIATOR else [item]
            answer = _contribution().to_dict()
            answer.update(tenant_id=TENANT, request_id=envelope["request_id"],
                          contribution_id="gateway-" + persona, participant_ref=persona,
                          event_content={"claims": [{"claim": "owner-backed claim", "evidence_refs": refs}],
                                         "summary_evidence_refs": refs},
                          evidence=[{"id": item, "evidence_type": "case_file_item", "link": "casefile://item"}])
            return SimpleNamespace(status="completed", output={
                "agent_id": kwargs["agent_id"], "json_events": [{"item": {"text": json.dumps(answer)}}]})

    adapter = create_app(provider=Gateway(), committee_memory_fetcher=memory,
                         provider_token=PROVIDER_TOKEN, expected_service_actor=wf.CONSUMER_NAME,
                         replay_path=stack["root"] / "adapter-replay.sqlite3")
    with _serve(adapter) as url:
        config = _config(stack, url + CONSULTATION_CONTRIBUTION_PATH)
        created = ConsultationServiceClient(base_url=stack["api"], token=SERVICE_TOKEN, tenant_id=TENANT).open_persona_panel(
            requirement, persona_id=INITIATOR, trace_id="trace-gateway")
        result = wf.run_tick(config=config, state=WorkflowStateStore(config.state_path), provider=_provider(config))
    if not memory_available:
        assert result["completed"] == 0 and not prompts
        assert "panel committee memory retrieval unavailable" in json.dumps(result)
        return
    assert result["completed"] == 1, result
    assert {(t, p) for t, p, q in queries} == {(TENANT, p) for p in personas}
    assert all("momentum" in q and "2330" in q and "trial admission" in q for t, p, q in queries)
    assert len(prompts) == len(personas)
    transcript = _get(stack["api"], f"/api/consult/requests/{created['request_id']}/transcript")
    rejection = next(e["content"] for e in transcript["events"] if e["event_type"] == "panel_contribution_rejected")
    assert rejection["persona_id"] == INITIATOR and rejection["reason"] == "claim_uncited"


def test_cross_tenant_registry_subject_is_rejected(stack, monkeypatch):
    real = wf.decision_case.http_json

    def other_tenant(endpoint, path, **kwargs):
        result = real(endpoint, path, **kwargs)
        if path.startswith("/api/registry/entries/"):
            return {**result, "tenant_id": "tenant-other"}
        return result

    monkeypatch.setattr(wf.decision_case, "http_json", other_tenant)
    config = _config(stack, "http://unused")
    with pytest.raises(wf.WorkflowBlocked, match="tenant binding mismatch"):
        wf._compose_panel_case_file(config, {
            "from_persona_id": INITIATOR, "request_id": "r1",
            "metadata": {"panel": {"touches": []}}}, {"type": "trial_admission", "id": "reg-x"})


def test_deployed_consultation_supplies_case_file_owners():
    from pathlib import Path
    import yaml
    env = yaml.safe_load(Path("docker-compose.yml").read_text())["services"]["consultation-svc"]["environment"]
    owners = wf.decision_case.Owners.from_env(env)
    assert all(getattr(owners, name) is not None for name in (
        "registry", "pool", "research", "telemetry", "memory", "search"))
    # Unset deployment credentials/context must stay empty, never become
    # a local service token or a privileged role.
    assert env["PANTHEON_PERSONA_SERVICE_TOKEN"] == "${PANTHEON_PERSONA_SERVICE_TOKEN:-}"
    assert env["PANTHEON_MEMORY_ACTOR_ID"] == "${PANTHEON_MEMORY_ACTOR_ID:-}"
    assert env["PANTHEON_MEMORY_ACTOR_ROLES"] == "${PANTHEON_MEMORY_ACTOR_ROLES:-}"


@pytest.mark.parametrize("key", ["PANTHEON_MEMORY_ACTOR_ID", "PANTHEON_MEMORY_ACTOR_ROLES"])
def test_case_composition_requires_explicit_caller_context(stack, monkeypatch, key):
    monkeypatch.delenv(key)
    with pytest.raises(wf.WorkflowBlocked, match="caller_context_missing"):
        wf._compose_panel_case_file(_config(stack, "http://unused"), {
            "request_id": "missing-context", "from_persona_id": INITIATOR,
            "metadata": {"panel": {"touches": []}},
        }, {"type": "trial_admission", "id": "reg-trial"})
