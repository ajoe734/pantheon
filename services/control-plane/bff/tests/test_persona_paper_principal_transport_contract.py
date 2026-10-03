"""Production wire/credential selection; isolated responses, never hosted proof."""
import io
import json

import pytest

from services.control_plane.bff.personas import service
from scripts.issue_dev_paper_principals import issue_environment


@pytest.fixture
def transport(monkeypatch):
    minted = issue_environment({
        "PANTHEON_ENV": "dev", "PANTHEON_DEV_BFF_TENANT_ID": "tenant-dev",
        "PANTHEON_DEV_PAPER_PRINCIPALS_AUTHORIZED": "true",
        "PANTHEON_DEV_BFF_JWT_SECRET": "synthetic-transport-verifier-secret-0001",
        "PANTHEON_DEV_BFF_JWT_ISSUER": "test-issuer",
        "PANTHEON_DEV_BFF_JWT_AUDIENCE": "test-audience",
    })
    monkeypatch.setenv("PANTHEON_PERSONA_GOVERNANCE_SERVICE_TOKEN", minted["PANTHEON_PERSONA_GOVERNANCE_SERVICE_TOKEN"])
    monkeypatch.setenv("PANTHEON_GOVERNANCE_APPROVAL_API_URL", "http://isolated-governance")
    requests = []

    def respond(request, **kwargs):
        requests.append(request)
        return io.BytesIO(b'{"isolated_response":true}')

    monkeypatch.setattr(service.urllib_request, "urlopen", respond)
    return service._PersonaOwnerHttpTransport(tenant_id="tenant-dev"), requests, minted


@pytest.mark.parametrize("path", ["/api/governance/approvals", "/api/governance/approvals/a/review", "/api/governance/approvals/a/decide"])
def test_real_transport_keeps_key_in_header_and_uses_issued_token(transport, path):
    client, requests, minted = transport
    payload = {"expected_version": 1, "actor_id": "pantheon-dev-paper-provisioner"}
    client.post("governance", path, payload)
    client.post("governance", path, payload)
    first, replay = requests
    assert first.get_header("Authorization") == "Bearer " + minted["PANTHEON_PERSONA_GOVERNANCE_SERVICE_TOKEN"]
    assert first.get_header("X-pantheon-service") == payload["actor_id"]
    assert first.get_header("Idempotency-key") == replay.get_header("Idempotency-key")
    assert first.get_header("Idempotency-key").startswith("persona-governance-")
    assert json.loads(first.data) == payload
    assert "idempotency_key" not in json.loads(first.data)
    client.post("governance", path, {**payload, "expected_version": 2})
    assert requests[-1].get_header("Idempotency-key") != first.get_header("Idempotency-key")
    client.post("governance", path + "/different", payload)
    assert requests[-1].get_header("Idempotency-key") != first.get_header("Idempotency-key")


def test_missing_issued_credential_never_self_grants_automation(transport, monkeypatch):
    client, requests, _ = transport
    monkeypatch.delenv("PANTHEON_PERSONA_GOVERNANCE_SERVICE_TOKEN")
    monkeypatch.setenv("PANTHEON_BFF_JWT_SECRET", "generic-key-exists-but-not-approval-authority")
    with pytest.raises(RuntimeError, match="Scoped Persona Governance principal"):
        client.get("governance", "/api/governance/approvals/a")
    assert not requests


def test_tenant_bound_credential_is_never_sent_for_another_tenant(transport):
    _, requests, _ = transport
    client = service._PersonaOwnerHttpTransport(tenant_id="foreign")
    with pytest.raises(RuntimeError, match="tenant-dev only"):
        client.get("governance", "/api/governance/approvals/a")
    assert not requests


def test_evaluator_bff_principal_reads_ranking_but_cannot_write(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from services.control_plane.bff.test_bff_management_delta_routes import _build_app, ManagementDeltaTestReadPorts

    for key, value in {"PANTHEON_BFF_AUTH_STUB": "false", "PANTHEON_BFF_AUTH_MODE": "strict",
                       "PANTHEON_BFF_JWT_SECRET": "synthetic-transport-verifier-secret-0001",
                       "PANTHEON_BFF_JWT_ISSUER": "test-issuer", "PANTHEON_BFF_JWT_AUDIENCE": "test-audience"}.items():
        monkeypatch.setenv(key, value)
    minted = issue_environment({
        "PANTHEON_ENV": "dev", "PANTHEON_DEV_BFF_TENANT_ID": "tenant-dev",
        "PANTHEON_DEV_PAPER_PRINCIPALS_AUTHORIZED": "true",
        "PANTHEON_DEV_BFF_JWT_SECRET": "synthetic-transport-verifier-secret-0001",
        "PANTHEON_DEV_BFF_JWT_ISSUER": "test-issuer", "PANTHEON_DEV_BFF_JWT_AUDIENCE": "test-audience",
    })
    headers = {"Authorization": "Bearer " + minted["PERSONA_EVALUATOR_BFF_TOKEN"]}
    client = TestClient(_build_app(ManagementDeltaTestReadPorts(allow_fallback=True)), raise_server_exceptions=False)
    assert client.get("/bff/management/quarterly-ranking", headers=headers, params={"quarter": "2026-Q1"}).status_code == 200
    denied = client.post("/bff/management/quarterly-ranking/recommendations/r1/submit",
                         headers={**headers, "Idempotency-Key": "evaluator-denied"}, json={"quarter": "2026-Q1"})
    assert denied.status_code == 403
