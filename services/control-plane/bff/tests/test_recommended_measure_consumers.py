"""Real candidate approval routes; consumer owners are faked at HTTP only."""
from __future__ import annotations

import copy
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from agora.candidate_decisions import consumers
from agora.candidate_decisions.adapters import CandidateBindingValidationAdapter, ReadStoreApprovalAdapter
from agora.candidate_decisions.models import canonical_sha256
from agora.candidate_decisions.router import create_candidate_decision_router
from agora.candidate_decisions.service import CandidateDecisionService
from agora.candidate_decisions.store import CandidateDecisionStore
from agora.interaction.provider import RecommendedMeasure, recommended_measure_sha256
from agora.interaction.store import InteractionLifecycleStore
from services.control_plane.bff.auth.policy import extract_identity, require_read_role, require_operator_role, bff_error
from services.runtime_auth_inbound import encode_jwt_hs256
from .test_agora_candidate_decisions import NOW, _interaction, _approval


VALUES = {
    "research_request": {"question": "Compare drawdown across regimes"},
    "memory_candidate": {"reflection_id": "c4034593-0bda-4fd6-9d30-a2b497b6fb92",
        "trade_episode_ids": [], "reflection_version": "v1", "proposed_change": "Avoid illiquid opens"},
    "persona_mutation_review": {"base_version": 1, "patch_type": "behavior",
        "proposed_change": {"identity": "Wait for sufficient evidence"},
        "evidence_refs": [{"kind": "packet", "ref": "packet-1", "moment": "Learn"}]},
}


class OwnerHTTP:
    def __init__(self):
        self.records = {}
        self.calls = []
        self.fail = False
        self.lose_response = False

    def __call__(self, method, url, payload=None, headers=None):
        self.calls.append((method, url, copy.deepcopy(payload), dict(headers or {})))
        if self.fail:
            return 503, {}
        if method == "GET":
            if url.endswith("/harness"):
                return 200, {"patches": {k.rsplit("/", 1)[1]: v for k, v in self.records.items() if "/harness/patches/" in k}}
            return (200, copy.deepcopy(self.records[url])) if url in self.records else (404, {})
        assert method == "POST"
        assert url.endswith(("/tickets", "/trade-lessons", "/patches"))
        field = "patch_id" if url.endswith("/patches") else "lesson_candidate_id" if url.endswith("/trade-lessons") else "ticket_id"
        if field == "patch_id":
            from services.persona.harness import PersonaPatch
            PersonaPatch.model_validate(payload)
        elif field == "lesson_candidate_id":
            from services.persona.lesson_governance import validate_lesson_candidate_json
            assert not validate_lesson_candidate_json(payload)
        row = {"proposal": payload, "status": "draft", "checks": []} if field == "patch_id" else payload
        if field == "ticket_id":
            from services.research.write_owner import ResearchWriteOwner
            row = ResearchWriteOwner._project_ticket_detail({**payload, "status": "open",
                "lifecycle_history": [{"from_status": None, "to_status": "open",
                    "transitioned_at": payload["created_at"], "transitioned_by": payload["actor_id"]}]})
        self.records[url + "/" + payload[field]] = copy.deepcopy(row)
        if self.lose_response:
            self.lose_response = False
            return 503, {}
        return 201, row


@pytest.fixture
def prepared(monkeypatch):
    monkeypatch.setenv("PANTHEON_BFF_TENANT_ID", "tenant-a")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_BFF_JWT_SECRET", "measure-integration-test-secret")
    monkeypatch.setenv("PANTHEON_BFF_JWT_ISSUER", "measure-tests")
    monkeypatch.setenv("PANTHEON_BFF_JWT_AUDIENCE", "pantheon-bff")
    monkeypatch.setenv("PANTHEON_BFF_MFA_REQUIRED", "false")
    token = encode_jwt_hs256({"sub": "operator-a", "user_id": "user-a", "roles": ["operator"],
        "tenant_id": "tenant-a", "iss": "measure-tests", "aud": "pantheon-bff",
        "iat": int(time.time()), "exp": int(time.time()) + 600}, secret="measure-integration-test-secret")
    for env, base in (("PANTHEON_RESEARCH_ORCHESTRATOR_API_URL", "http://research"),
                      ("PANTHEON_MEMORY_API_URL", "http://memory"), ("PERSONA_URL", "http://persona")):
        monkeypatch.setenv(env, base)
    owner = OwnerHTTP()
    monkeypatch.setattr(consumers, "default_http_request", owner)
    # Seed canonical upstream approval readback; never bypass the production
    # checksum, provenance, validation, reviewer, tenant or revision checks.
    approvals = {}
    read_port = SimpleNamespace(get_canonical_approval_decision_readback=lambda key: {
        "available": True, "record": approvals.get(key)})
    service = CandidateDecisionService(CandidateDecisionStore(backend="memory"),
        interaction_store=InteractionLifecycleStore(backend="memory"),
        validation_adapter=CandidateBindingValidationAdapter(clock=lambda: NOW),
        approval_store=ReadStoreApprovalAdapter(lambda: read_port), clock=lambda: NOW)
    app = FastAPI()
    app.include_router(create_candidate_decision_router(service=service,
        extract_identity=extract_identity, require_read_role=require_read_role,
        require_write_role=require_operator_role, bff_error=bff_error,
        utc_now=lambda: NOW.isoformat()))
    return SimpleNamespace(service=service, owner=owner, approvals=approvals, client=TestClient(app), auth="Bearer " + token)


def stage(p, kind, value=None):
    interaction = _interaction()
    interaction.update(owner_user_id="user-a", workshop_id="workshop-a", status="completed",
        created_at=NOW.isoformat(), updated_at=NOW.isoformat())
    measure = interaction["opinions"][0]["recommended_measures"][0]
    measure["measure_type"] = kind
    measure["proposed_value"] = copy.deepcopy(value if value is not None else VALUES.get(kind, {}))
    if kind == "persona_mutation_review":
        measure["target"] = {"kind": "persona", "id": "persona-risk", "version": "1"}
    measure.pop("measure_sha256", None)
    measure = RecommendedMeasure.model_validate(measure).model_dump(mode="json")
    interaction["opinions"][0]["recommended_measures"][0] = measure
    measure["measure_sha256"] = recommended_measure_sha256(measure)
    p.service.interaction_store._requests["interaction-1"] = interaction
    opinion = interaction["opinions"][0]
    p.service.interaction_store._invocations["interaction-1"] = {
        opinion["provider_invocation_id"]: {"invocation": {
            "invocation_id": opinion["provider_invocation_id"], "participant": opinion["participant"],
            "status": "succeeded"}, "opinion": opinion, "error": None, "status": "succeeded"}}
    headers = {"Authorization": p.auth, "Idempotency-Key": "create", "X-Tenant-Id": "tenant-a"}
    response = p.client.post("/bff/agora/interactions/interaction-1/recommended-measures/measure-risk-1/candidates",
        headers=headers, json={"interaction_id": "interaction-1", "opinion_id": "opinion-1", "measure_id": "measure-risk-1"})
    assert response.status_code == 201, response.text
    candidate = response.json()["data"]["candidate"]
    base = "/bff/agora/proposals/" + candidate["proposal_id"]
    binding = lambda c: {"expected_revision": c["revision"], "expected_proposal_digest": c["proposal_digest"]}
    response = p.client.post(base + "/candidate-decisions", headers={**headers, "If-Match": response.headers["etag"], "Idempotency-Key": "accept"},
        json={"action": "accept_for_review", "reason": "Review this measure", **binding(candidate)})
    assert response.status_code == 200, response.text
    candidate = response.json()["data"]["candidate"]
    response = p.client.post(base + "/validations", headers={**headers, "If-Match": response.headers["etag"], "Idempotency-Key": "validate"}, json=binding(candidate))
    assert response.status_code == 200, response.text
    validation = response.json()["data"]["validation_receipts"][-1]
    assert validation["outcome"] == "passed"
    p.approvals["approval-1"] = _approval(candidate, validation)
    return base, {**headers, "If-Match": response.headers["etag"], "Idempotency-Key": "approval"}, binding(candidate)


def approve(p, staged):
    base, headers, body = staged
    return p.client.post(base + "/formal-approvals/approval-1:link", headers=headers, json=body)


@pytest.mark.parametrize("kind", VALUES)
def test_real_approval_route_delivers_each_consumer_and_replays(prepared, kind):
    p = prepared
    staged = stage(p, kind)
    response = approve(p, staged)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    receipt = data["downstream_receipts"][0]
    assert receipt["consumer"] == kind
    assert receipt["measure_id"] == "measure-risk-1"
    assert receipt["persona_id"] == "persona-risk"
    assert receipt["execution_authority"] == data["execution_authority"] == "none"
    writes = [c for c in p.owner.calls if c[0] == "POST"]
    assert len(writes) == 1
    _, url, payload, headers = writes[0]
    assert headers["Authorization"] == p.auth
    assert headers["X-Tenant-Id"] == "tenant-a"
    assert headers["Idempotency-Key"] == receipt["idempotency_key"]
    if kind == "research_request":
        assert payload["owner"] == "persona-risk"
    elif kind == "memory_candidate":
        assert payload["review_state"] == "proposed"
        assert payload["promotion_stage"] == "proposed"
    else:
        assert payload["persona_id"] == "persona-risk"
    for request in (staged, (staged[0], {**staged[1], "Idempotency-Key": "another-key"}, staged[2])):
        replay = approve(p, request)
        assert replay.status_code == 200, replay.text
        assert replay.json()["data"]["downstream_receipts"] == [receipt]
    reloaded = p.client.get(staged[0] + "/candidate", headers=staged[1])
    assert reloaded.json()["data"]["downstream_receipts"] == [receipt]
    assert len([c for c in p.owner.calls if c[0] == "POST"]) == 1
    assert len(data["formal_approval_receipts"]) == 1


@pytest.mark.parametrize("kind", VALUES)
def test_owner_write_lost_response_is_recovered_by_id_without_duplicate(prepared, kind):
    p = prepared
    staged = stage(p, kind)
    p.owner.lose_response = True
    first = approve(p, staged)
    assert first.status_code == 409
    assert not p.service.store.approval_receipts(staged[0].rsplit("/", 1)[-1], "tenant-a", "user-a")
    second = approve(p, staged)
    assert second.status_code == 200, second.text
    assert len([c for c in p.owner.calls if c[0] == "POST"]) == 1


@pytest.mark.parametrize("kind", ["strategy_parameter_change", "condition_change", "risk_limit_recommendation",
    "paper_candidate_request", "allocation_review_request", "containment_recommendation", "journal_lesson"])
def test_unconsumed_measures_are_refused_at_approval(prepared, kind):
    p = prepared
    staged = stage(p, kind)
    response = approve(p, staged)
    assert response.status_code == 409, response.text
    assert "no downstream consumer" in response.text
    assert p.owner.calls == []
    readback = p.client.get(staged[0] + "/candidate", headers=staged[1]).json()["data"]
    assert readback["formal_approval_receipts"] == readback["downstream_receipts"] == []


def test_unavailable_owner_and_stale_etag_never_write_approval(prepared):
    p = prepared
    staged = stage(p, "research_request")
    p.owner.fail = True
    assert approve(p, staged).status_code == 409
    p.owner.fail = False
    stale = (staged[0], {**staged[1], "If-Match": '"stale"'}, staged[2])
    assert approve(p, stale).status_code == 409
    assert not [c for c in p.owner.calls if c[0] == "POST"]
    assert approve(p, staged).status_code == 200


def test_divergent_owner_receipt_is_not_accepted(prepared):
    p = prepared
    staged = stage(p, "research_request")
    p.owner.lose_response = True
    assert approve(p, staged).status_code == 409
    # The owner committed but its response was lost. Refuse recovery if those
    # bytes now belong to a different persona.
    for record in p.owner.records.values():
        record["owner"] = "another-persona"
    response = approve(p, staged)
    assert response.status_code == 409
    assert "does not match" in response.text


def test_concurrent_approval_calls_create_one_owner_record(prepared):
    p = prepared
    staged = stage(p, "research_request")
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: approve(p, staged), range(2)))
    assert [r.status_code for r in results] == [200, 200]
    assert len([c for c in p.owner.calls if c[0] == "POST"]) == 1


@pytest.fixture
def postgres_pair():
    import os
    import uuid
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL is not set")
    schema = "measure_consumer_" + uuid.uuid4().hex[:12]
    first = CandidateDecisionStore(backend="postgres", dsn=dsn, schema=schema)
    second = CandidateDecisionStore(backend="postgres", dsn=dsn, schema=schema)
    try:
        yield first, second
    finally:
        with first._connect() as conn:
            conn.execute(f'DROP SCHEMA "{schema}" CASCADE')


def migrate_staged(p, staged, target):
    proposal = staged[0].rsplit("/", 1)[-1]
    current = p.service.store.get(proposal, "tenant-a", "user-a")
    validations = p.service.store.validation_receipts(proposal, "tenant-a", "user-a")
    target.create_candidate(current, idempotency_key="pg-create", fingerprint=canonical_sha256(current))
    for validation in validations:
        target.record_validation(current=current, expected_etag=target.etag(current), receipt=validation,
            idempotency_key=validation["validation_receipt_id"], fingerprint=canonical_sha256(validation))
    p.service.store = target
    return current


@pytest.mark.parametrize("kind", VALUES)
def test_postgres_real_approval_restart_recovers_owner_write(prepared, postgres_pair, kind):
    p = prepared
    first, restarted = postgres_pair
    staged = stage(p, kind)
    current = migrate_staged(p, staged, first)
    p.owner.lose_response = True
    assert approve(p, staged).status_code == 409
    assert restarted.approval_receipts(current["proposal_id"], "tenant-a", "user-a") == []
    assert restarted.consumer_receipts(current) == []
    p.service.store = restarted
    response = approve(p, staged)
    assert response.status_code == 200, response.text
    receipt = response.json()["data"]["downstream_receipts"][0]
    assert first.consumer_receipts(current) == [receipt]
    assert len(first.approval_receipts(current["proposal_id"], "tenant-a", "user-a")) == 1
    assert len([c for c in p.owner.calls if c[0] == "POST"]) == 1
    assert approve(p, staged).status_code == 200
    assert first.get(current["proposal_id"], "tenant-a", "different-user") is None


def test_postgres_concurrent_approval_is_one_delivery(prepared, postgres_pair):
    p = prepared
    first, second = postgres_pair
    staged = stage(p, "research_request")
    current = migrate_staged(p, staged, first)
    approval = p.approvals["approval-1"]
    def record(store):
        plan = p.service.consumers.prepare(current, "Bearer caller")
        return store.record_approval(current=current, expected_etag=store.etag(current), receipt=approval,
            idempotency_key="race-approval", fingerprint=canonical_sha256(approval), consumer=plan)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(record, [first, second]))
    assert sorted(r.replayed for r in results) == [False, True]
    assert len([c for c in p.owner.calls if c[0] == "POST"]) == 1
    assert len(second.consumer_receipts(current)) == 1


@pytest.mark.parametrize("kind,value", [
    ("memory_candidate", {"proposed_change": "missing real reflection provenance"}),
    ("persona_mutation_review", {**VALUES["persona_mutation_review"], "base_version": 2}),
])
def test_incomplete_or_mismatched_owner_payload_is_refused(prepared, kind, value):
    p = prepared
    staged = stage(p, kind, value)
    assert approve(p, staged).status_code == 409
    assert not p.owner.calls


def test_persona_draft_creation_can_use_existing_service_identity(prepared, monkeypatch):
    p = prepared
    monkeypatch.setenv("PANTHEON_PERSONA_SERVICE_TOKEN", "existing-test-persona-principal")
    staged = stage(p, "persona_mutation_review")
    assert approve(p, staged).status_code == 200
    writes = [c for c in p.owner.calls if c[0] == "POST"]
    assert writes[0][3]["Authorization"] == "Bearer existing-test-persona-principal"
    assert writes[0][1].endswith("/harness/patches")


def test_approved_measure_cannot_be_modified_or_redelivered(prepared):
    p = prepared
    staged = stage(p, "research_request")
    response = approve(p, staged)
    assert response.status_code == 200
    assert response.json()["data"]["readiness"]["candidate"]["allowed_actions"] == []
    modified = p.client.post(staged[0] + "/candidate-decisions", headers={**staged[1], "Idempotency-Key": "modify-after-approval"},
        json={**staged[2], "action": "modify", "reason": "alter already consumed bytes", "proposed_value": {"different": True}})
    assert modified.status_code == 409
    assert len([c for c in p.owner.calls if c[0] == "POST"]) == 1
