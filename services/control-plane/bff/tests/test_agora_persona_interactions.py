from __future__ import annotations

import os
import sys

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import main as bff_main

AUTH = {"Authorization": "Bearer interaction-user:operator", "Idempotency-Key": "idem-context-1"}


class FakeReadStore:
    def list_personas(self, **kwargs):
        return [
            {"persona_id": "ready", "display_name": "Ready", "lifecycle_state": "active", "environment_ceiling": "paper"},
            {"persona_id": "draft", "display_name": "Draft", "lifecycle_state": "draft", "environment_ceiling": "paper"},
            {"persona_id": "research", "display_name": "Research", "lifecycle_state": "active", "environment_ceiling": "research"},
        ]

    def get_capability_snapshot_for_persona(self, persona_id):
        return {"snapshot_id": f"snap-{persona_id}", "capabilities": ["persona_opinion"]}


def client(monkeypatch):
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
    monkeypatch.setattr(bff_main, "read_store", FakeReadStore())
    return TestClient(bff_main.app, raise_server_exceptions=False)


def context_payload(version="v1"):
    return {"environment": "paper", "context_refs": [
        {"type": "strategy", "id": "strategy-1", "version_id": version},
        {"type": "decision_event", "id": "decision-1"},
    ]}


def test_context_resolution_is_idempotent_and_versioned(monkeypatch):
    c = client(monkeypatch)
    first = c.post("/bff/agora/interactions/context:resolve", headers=AUTH, json=context_payload())
    second = c.post("/bff/agora/interactions/context:resolve", headers=AUTH, json=context_payload())
    assert first.status_code == second.status_code == 200, first.text
    assert first.json()["data"] == second.json()["data"]
    assert first.json()["data"]["verified"] is True
    invalid = c.post("/bff/agora/interactions/context:resolve", headers={**AUTH, "Idempotency-Key": "invalid"},
                     json={"context_refs": [{"type": "strategy", "id": "strategy-1"}]})
    assert invalid.status_code == 422


def test_eligibility_includes_and_excludes_with_reasons(monkeypatch):
    c = client(monkeypatch)
    resolved = c.post("/bff/agora/interactions/context:resolve", headers=AUTH, json=context_payload()).json()["data"]
    response = c.post("/bff/agora/interactions/participants:eligible", headers={"Authorization": AUTH["Authorization"]}, json={
        "workshop_id": resolved["workshop_id"], "mode": "consult", "environment": "paper",
        "required_capability": "persona_opinion",
    })
    assert response.status_code == 200, response.text
    body = response.json()["data"]
    assert [x["persona_id"] for x in body["included"]] == ["ready"]
    excluded = {x["persona_id"]: x["reasons"] for x in body["excluded"]}
    assert "persona_not_active" in excluded["draft"]
    assert "environment_ceiling_exceeded" in excluded["research"]


def test_typed_submission_is_idempotent_and_has_no_write_authority(monkeypatch):
    c = client(monkeypatch)
    resolved = c.post("/bff/agora/interactions/context:resolve", headers=AUTH, json=context_payload()).json()["data"]
    payload = {"workshop_id": resolved["workshop_id"], "mode": "consult", "environment": "paper",
        "topic": "Review risk", "participant_persona_ids": ["ready"], "context_refs": context_payload()["context_refs"]}
    headers = {**AUTH, "Idempotency-Key": "interaction-1"}
    first = c.post("/bff/agora/interactions", headers=headers, json=payload)
    second = c.post("/bff/agora/interactions", headers=headers, json=payload)
    assert first.status_code == second.status_code == 202, first.text
    assert first.json()["data"] == second.json()["data"]
    assert first.json()["data"]["execution_authority"] == "none"


def test_ineligible_participant_and_cross_tenant_fail_closed(monkeypatch):
    c = client(monkeypatch)
    resolved = c.post("/bff/agora/interactions/context:resolve", headers=AUTH, json=context_payload()).json()["data"]
    payload = {"workshop_id": resolved["workshop_id"], "mode": "ask", "environment": "paper", "topic": "Explain",
        "participant_persona_ids": ["draft"], "context_refs": context_payload()["context_refs"]}
    assert c.post("/bff/agora/interactions", headers={**AUTH, "Idempotency-Key": "bad-persona"}, json=payload).status_code == 422
    monkeypatch.setenv("PANTHEON_BFF_ALLOWED_TENANTS", "pantheon-dev")
    response = c.post("/bff/agora/interactions/participants:eligible",
        headers={"Authorization": AUTH["Authorization"], "X-Tenant-Id": "other"},
        json={"workshop_id": resolved["workshop_id"], "mode": "ask"})
    assert response.status_code == 403
