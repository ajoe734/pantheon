from __future__ import annotations

import os
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from services.control_plane.bff import main as bff_main
from services.runtime_auth_inbound import encode_jwt_hs256


class _ReadStore:
    def list_personas(self, **_kwargs):
        return [
            {
                "persona_id": "authority-ready",
                "tenant_id": "pantheon-dev",
                "display_name": "Authority Ready",
                "lifecycle_state": "active",
                "environment_ceiling": "paper",
            }
        ]

    def get_capability_snapshot_for_persona(self, persona_id):
        return {
            "snapshot_id": f"snap-{persona_id}",
            "capabilities": ["persona_opinion"],
        }

    def list_approval_decisions(self):
        return []


def _client(monkeypatch) -> TestClient:
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
    monkeypatch.setattr(bff_main, "read_store", _ReadStore())
    return TestClient(bff_main.app, raise_server_exceptions=False)


def _headers(role: str, *, idempotency_key: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer authority-user:{role}"}
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    return headers


def _capability_scoped_viewer(monkeypatch) -> dict[str, str]:
    secret = "agora-write-authority-test-secret"
    issuer = "agora-write-authority-test"
    audience = "pantheon-bff"
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_BFF_JWT_SECRET", secret)
    monkeypatch.setenv("PANTHEON_BFF_JWT_ISSUER", issuer)
    monkeypatch.setenv("PANTHEON_BFF_JWT_AUDIENCE", audience)
    monkeypatch.setenv("PANTHEON_BFF_MFA_REQUIRED", "false")
    now = int(time.time())
    token = encode_jwt_hs256(
        {
            "sub": "authority-viewer",
            "user_id": "authority-user",
            "roles": ["viewer"],
            "capabilities": ["agora.workshop.v1"],
            "iss": issuer,
            "aud": audience,
            "iat": now,
            "exp": now + 3600,
        },
        secret=secret,
    )
    return {"Authorization": f"Bearer {token}"}


def _assert_write_forbidden(response) -> None:
    assert response.status_code == 403, response.text
    error = response.json()["error"]
    assert error["code"] == "FORBIDDEN"
    assert error["details"]["reason"] == "Operator does not hold the required command role"
    assert error["details"]["precondition_failed"] == "role_check"


def _proposal_payload() -> dict:
    return {
        "proposal_type": "strategy_patch",
        "target_kind": "strategy",
        "target_id": "authority-strategy",
        "target_version": "v1",
        "current_value": {"risk": 0.1},
        "proposed_value": {"risk": 0.08},
        "rationale": "reduce drawdown",
        "evidence_refs": ["authority-evidence"],
        "confidence": 0.8,
        "expected_benefit": "lower drawdown",
        "adverse_scenarios": ["missed upside"],
        "environment_ceiling": "paper",
        "expires_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        "validation_plan": {"backtest": "authority-bt"},
        "rollback_trigger": "drawdown worsens",
        "rollback_action": "restore v1",
        "required_permissions": ["strategy.review"],
        "required_reviewers": ["risk"],
        "human_gate": True,
    }


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/bff/agora/workshops/authority-workshop/messages", {"content": "bypass"}),
        ("/bff/agora/workshops/authority-workshop/completeness", {}),
        ("/bff/agora/workshops/authority-workshop/readiness/reassess", {}),
        ("/bff/agora/workshops/authority-workshop/versions", None),
        ("/bff/agora/workshops/authority-workshop/versions/v1/select", None),
        ("/bff/agora/workshops/authority-workshop/research-runs", None),
        ("/bff/agora/workshops/authority-workshop/consultations", None),
        ("/bff/agora/workshops/authority-workshop/conclude", None),
    ],
)
def test_viewer_cannot_call_any_workshop_mutation(monkeypatch, path, body):
    client = _client(monkeypatch)
    kwargs = {"headers": _headers("viewer")}
    if body is not None:
        kwargs["json"] = body
    _assert_write_forbidden(client.post(path, **kwargs))


def test_admin_can_use_shared_agora_mutation_role(monkeypatch):
    client = _client(monkeypatch)
    suffix = uuid.uuid4().hex
    admin = _headers("admin")

    workshop = client.post(
        "/bff/agora/workshops",
        headers={**admin, "Idempotency-Key": f"workshop-admin-{suffix}"},
        json={"initial_message": "Admin authority regression"},
    )
    assert workshop.status_code == 201, workshop.text

    proposal = client.post(
        "/bff/agora/proposals",
        headers={**admin, "Idempotency-Key": f"proposal-admin-{suffix}"},
        json=_proposal_payload(),
    )
    assert proposal.status_code == 201, proposal.text


def test_viewer_cannot_bypass_agora_write_authority(monkeypatch):
    client = _client(monkeypatch)
    suffix = uuid.uuid4().hex
    operator = _headers("operator")
    viewer = _headers("viewer")

    workshop_body = {"initial_message": "Review authority boundaries"}
    created_workshop = client.post(
        "/bff/agora/workshops",
        headers={**operator, "Idempotency-Key": f"workshop-operator-{suffix}"},
        json=workshop_body,
    )
    assert created_workshop.status_code == 201, created_workshop.text
    direct_workshop_id = created_workshop.json()["data"]["workshop_id"]
    current = client.get(
        f"/bff/agora/workshops/{direct_workshop_id}",
        headers=operator,
    )
    posted_message = client.post(
        f"/bff/agora/workshops/{direct_workshop_id}/messages",
        headers={
            **operator,
            "Idempotency-Key": f"message-operator-{suffix}",
            "If-Match": current.headers["etag"],
        },
        json={"content": "Operator can mutate the workshop"},
    )
    assert posted_message.status_code == 202, posted_message.text
    _assert_write_forbidden(
        client.post(
            "/bff/agora/workshops",
            headers={**viewer, "Idempotency-Key": f"workshop-viewer-{suffix}"},
            json=workshop_body,
        )
    )

    context = {
        "environment": "paper",
        "context_refs": [
            {"type": "strategy", "id": "authority-strategy", "version_id": "v1"}
        ],
    }
    resolved = client.post(
        "/bff/agora/interactions/context:resolve",
        headers={**operator, "Idempotency-Key": f"context-operator-{suffix}"},
        json=context,
    )
    assert resolved.status_code == 200, resolved.text
    workshop_id = resolved.json()["data"]["workshop_id"]
    _assert_write_forbidden(
        client.post(
            "/bff/agora/interactions/context:resolve",
            headers={**viewer, "Idempotency-Key": f"context-viewer-{suffix}"},
            json=context,
        )
    )

    capability_viewer = _capability_scoped_viewer(monkeypatch)
    eligibility = client.post(
        "/bff/agora/interactions/participants:eligible",
        headers=capability_viewer,
        json={
            "workshop_id": workshop_id,
            "mode": "consult",
            "environment": "paper",
            "required_capability": "persona_opinion",
        },
    )
    assert eligibility.status_code == 200, eligibility.text

    interaction = {
        "workshop_id": workshop_id,
        "mode": "consult",
        "environment": "paper",
        "topic": "Review risk",
        "participant_persona_ids": ["authority-ready"],
        "context_refs": context["context_refs"],
    }
    _assert_write_forbidden(
        client.post(
            "/bff/agora/interactions",
            headers={**capability_viewer, "Idempotency-Key": f"interaction-viewer-{suffix}"},
            json=interaction,
        )
    )
    _assert_write_forbidden(
        client.post(
            "/bff/agora/interactions",
            headers={**capability_viewer, "Idempotency-Key": f"propose-viewer-{suffix}"},
            json={**interaction, "mode": "propose_action"},
        )
    )
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
    submitted = client.post(
        "/bff/agora/interactions",
        headers={**operator, "Idempotency-Key": f"interaction-operator-{suffix}"},
        json=interaction,
    )
    assert submitted.status_code == 202, submitted.text

    proposal = client.post(
        "/bff/agora/proposals",
        headers={**operator, "Idempotency-Key": f"proposal-operator-{suffix}"},
        json=_proposal_payload(),
    )
    assert proposal.status_code == 201, proposal.text
    proposal_id = proposal.json()["data"]["proposal_id"]
    capability_viewer = _capability_scoped_viewer(monkeypatch)
    _assert_write_forbidden(
        client.post(
            "/bff/agora/proposals",
            headers={**capability_viewer, "Idempotency-Key": f"proposal-viewer-{suffix}"},
            json=_proposal_payload(),
        )
    )
    _assert_write_forbidden(
        client.post(
            f"/bff/agora/proposals/{proposal_id}/actions",
            headers={**capability_viewer, "If-Match": proposal.headers["etag"]},
            json={"action": "modify", "reason": "viewer bypass", "proposed_value": {"risk": 0.07}},
        )
    )
    latest = client.get(
        f"/bff/agora/proposals/{proposal_id}",
        headers=capability_viewer,
    )
    assert latest.status_code == 200, latest.text
    assert latest.json()["data"]["revision"] == 1

    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
    modified = client.post(
        f"/bff/agora/proposals/{proposal_id}/actions",
        headers={**operator, "If-Match": proposal.headers["etag"]},
        json={"action": "modify", "reason": "operator review", "proposed_value": {"risk": 0.07}},
    )
    assert modified.status_code == 200, modified.text
