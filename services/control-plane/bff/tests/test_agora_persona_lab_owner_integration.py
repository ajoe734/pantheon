"""Persona lab routes through the REAL persona owner app (no recording stand-in).

The BFF router's transport is bridged into the real persona owner FastAPI app
(CAS-persisted harness state, real precheck, real shadow evaluator, real
adoption/outbox). Only the replay model answers and the downstream receiving
owners' HTTP receipts are fixtures.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from services.control_plane.bff.agora.persona_lab.router import create_persona_lab_router
from services.persona.harness_shadow_eval import DenyAllReplayProvider
from services.persona.test_harness import ROOT, headers, initialize, receipt, system  # noqa: F401
from services.persona.test_harness_adoption import delivery_receipt
from services.persona.test_harness_shadow_eval import REPLAY_AGENT, DENY_ALL_POLICY, remove, seed, turn

P = "/bff/agora/persona-lab/p-alpha"
H = headers(roles=["operator"])  # real signed operator JWT: no persona.admin
THRESHOLD = {"cycle_thresholds": {"minimum_evidence_count": 3, "minimum_confidence": 0.8}}


@pytest.fixture
def lab(system, tmp_path, monkeypatch):  # noqa: F811
    owner_client, owner, harness, _, receipts, evidence = system
    packets = tmp_path / "packets.jsonl"
    seed(packets, 3)
    harness.packet_store_path = lambda: packets
    harness.shadow_provider = lambda pid: DenyAllReplayProvider(
        REPLAY_AGENT, lambda a: DENY_ALL_POLICY,
        lambda a, prompt: turn([remove(3, 0.9)] if '"version": "candidate"' in prompt else [remove(1, 0.2)]))
    deliveries = []
    harness.adoption.sender = lambda kind, payload: deliveries.append((kind, payload)) or delivery_receipt(kind, payload)
    monkeypatch.setenv("PANTHEON_PERSONA_API_URL", "http://owner")
    monkeypatch.setenv("PANTHEON_MEMORY_API_URL", "http://memory")
    monkeypatch.setenv("PANTHEON_PERSONA_SERVICE_TOKEN", "bff-owner-principal")

    def transport(method, url, payload, hdrs):
        assert url.startswith("http://owner"), url
        response = owner_client.request(method, url[len("http://owner"):], json=payload, headers=hdrs)  # real forwarded headers
        try:
            return response.status_code, response.json()
        except ValueError:
            return response.status_code, None

    def error(status, code, message, reason, **kw):
        return HTTPException(status_code=status, detail={"code": str(code), "reason": reason})

    app = FastAPI()
    app.include_router(create_persona_lab_router(
        extract_identity=lambda authorization, mfa_token=None: {"operator_id": "u1", "tenant_id": "tenant-a"},
        require_read_role=lambda i: None, require_write_role=lambda i: None,
        bff_error=error, utc_now=lambda: "2026-11-13T00:00:00Z", http_request=transport))
    initialize(system)
    return TestClient(app), system, deliveries


def draft(client, patch_id="patch-1", route="/patches", evidence=None, **change):
    body = {"patch_id": patch_id, "session_id": "s1", "base_version": 1,
            "patch_type": "skill" if "skills" in change else "threshold",
            "proposed_change": change or THRESHOLD, "evidence_refs": [evidence],
            "expected_effect": "tighten evidence", "rollback_target": 1}
    return client.post(P + route, headers=H, json=body)


def test_draft_shadow_comparison_evaluation_and_submit_through_real_owner(lab):
    client, system, deliveries = lab
    evidence = system[-1]
    assert draft(client, evidence=evidence).status_code == 201
    run = client.post(P + "/patches/patch-1/shadow-runs", headers=H)
    assert run.status_code == 201, run.text
    record = run.json()["data"]
    assert record["status"] == "complete" and record["replay"]["deny_all_verified"] is True
    view = client.get(P + "/patches/patch-1/shadow-comparison", headers=H).json()["data"]
    assert view["arms"] == ["base", "candidate"] and len(view["rows"]) == 3
    assert all(r["decision_time"] and r["base"] and r["candidate"] for r in view["rows"])
    runs = client.get(P + "/evaluation/runs", headers=H).json()["data"]
    assert runs[0]["patch_id"] == "patch-1" and runs[0]["bundle_count"] == 3
    assert client.get(P + "/evaluation/suites", headers=H).json()["data"][0]["runs"] == 1
    submitted = client.post(P + "/patches/patch-1/submit", headers=H, json=receipt(system))
    assert submitted.status_code == 201, submitted.text
    data = submitted.json()["data"]
    assert data["review_status"] == "committed" and data["committed"]["status"] == "committed"
    log = client.get(P + "/change-log", headers=H).json()["data"]
    assert log["active_version"] == 2 and log["entries"][-1]["evidence_refs"]
    undone = client.post(P + "/change-log/patch-1/undo", headers=H, json={"reason": "operator undo"})
    assert undone.status_code == 200 and undone.json()["data"]["status"] == "rolled_back"
    assert client.get(P + "/change-log", headers=H).json()["data"]["active_version"] == 1


def test_skill_coaching_uses_the_same_owner_pipeline(lab):
    client, system, _ = lab
    from services.persona.test_harness import BASE
    skill = {**BASE["skills"][0], "guidance": "Coach: cite evidence first."}
    response = draft(client, "skill-1", "/skill-coaching/patches", system[-1], skills=[skill])
    assert response.status_code == 201, response.text
    assert response.json()["data"]["proposal"]["patch_type"] == "skill"
    assert client.post(P + "/patches/skill-1/shadow-runs", headers=H).status_code == 201


def test_outside_paper_submit_resolves_governance_review_and_confirms_delivery(lab):
    client, system, deliveries = lab
    owner = system[1]
    raw = owner._records.get("p-alpha")
    assert owner._records.compare_and_set("p-alpha", raw, {**raw, "lifecycle_state": "live_owner"})[0]
    assert draft(client, "outside", evidence=system[-1]).status_code == 201
    submitted = client.post(P + "/patches/outside/submit", headers=H, json={})
    assert submitted.status_code == 201, submitted.text
    data = submitted.json()["data"]
    assert data["review_status"] == "delivered" and data["review_ref"].startswith("harness-governance-")
    assert data["review_receipt"]["decision_id"] == data["review_ref"]
    assert any(kind == "governance" for kind, _ in deliveries)
    # Fail closed when the management review has not consumed the handoff.
    system[2].adoption.sender = lambda kind, payload: (_ for _ in ()).throw(RuntimeError("down"))
    state = system[2].read("p-alpha")
    state_ref = data["review_ref"]
    def reset(persona, st):
        st["outbox"][state_ref].update(status="pending", receipt=None)
        return st, {}
    system[2]._mutate("p-alpha", "t", reset, paper_only=False)
    refused = client.post(P + "/patches/outside/submit", headers=H, json={})
    assert refused.status_code == 502 and refused.json()["detail"]["reason"] == "management_review_not_delivered"


def test_never_again_writes_owner_negative_experience_consumed_by_next_proposal(lab):
    client, system, deliveries = lab
    assert draft(client, "p-old", evidence=system[-1]).status_code == 201
    r = client.post(P + "/change-log/p-old/never-again", headers=H, json={"reason": "caused whipsaw"})
    assert r.status_code == 201, r.text
    experience = r.json()["data"]["operator_feedback"]
    assert experience["outcome"] == "never_again" and "caused whipsaw" in experience["lesson"]
    assert any(kind == "memory" and p["source_event_id"] == experience["source_event_id"] for kind, p in deliveries)
    assert draft(client, "p-new", evidence=system[-1]).status_code == 201
    refs = system[2].read("p-alpha")["patches"]["p-new"]["experience_refs"]
    assert [e["source_event_id"] for e in refs] == [experience["source_event_id"]]
    assert client.post(P + "/change-log/p-old/never-again", headers=H, json={}).status_code == 422


def test_operator_bearer_alone_is_refused_and_missing_principal_fails_closed(lab, monkeypatch):
    client, system, _ = lab
    # Forwarded headers are real: the operator bearer never reaches the owner on writes.
    assert draft(client, evidence=system[-1]).status_code == 201
    monkeypatch.delenv("PANTHEON_PERSONA_SERVICE_TOKEN")
    monkeypatch.delenv("PERSONA_SERVICE_TOKEN", raising=False)
    res = draft(client, patch_id="patch-2", evidence=system[-1])
    assert res.status_code == 503 and res.json()["detail"]["reason"] == "persona_owner_service_token_unconfigured"
    assert client.post(P + "/patches/patch-1/shadow-runs", headers=H).status_code == 503
    assert client.post(P + "/change-log/patch-1/never-again", headers=H, json={"reason": "x"}).status_code == 503


def test_owner_tenant_gate_still_enforced_with_service_principal(lab):
    client, system, _ = lab
    res = client.post(P + "/patches", headers={**H, "X-Tenant-Id": "tenant-b"}, json={"patch_id": "x"})
    assert res.status_code == 403, res.text
