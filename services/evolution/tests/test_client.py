"""
Tests for EvolutionClient authentication, bearer tokens, X-Tenant-Id, idempotency, and readback verification.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Allow import of parent package
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services.evolution.client import (
    EvolutionAuthenticationError,
    EvolutionClient,
    EvolutionClientError,
    EvolutionReadbackError,
)


class DummyResponse:
    def __init__(self, status_code, json_data=None, text=""):
        self.status_code = status_code
        if isinstance(json_data, str):
            self.text = json_data
            self._json_data = {"detail": json_data}
        else:
            self._json_data = json_data or {}
            self.text = text or (json_data.get("detail", "") if isinstance(json_data, dict) else "")

    def json(self):
        return self._json_data


@pytest.mark.anyio
async def test_evolution_client_token_mode_success():

    class DummyClient:
        def __init__(self):
            self.requests = []

        async def post(self, url, json, headers):
            self.requests.append(("POST", url, json, headers))
            return DummyResponse(
                201,
                {
                    "decision_id": "dec-100",
                    "status": "proposed",
                    "source_postmortem_id": "pm-100",
                },
            )

        async def get(self, url, headers):
            self.requests.append(("GET", url, None, headers))
            return DummyResponse(
                200,
                {
                    "decision_id": "dec-100",
                    "status": "proposed",
                    "source_postmortem_id": "pm-100",
                },
            )

    dummy_http = DummyClient()
    client = EvolutionClient(
        base_url="http://evolution-test:8093",
        auth_token="secret-token-123",
        tenant_id="tenant-test-1",
        async_client=dummy_http,
    )

    proposal = {
        "source_postmortem_id": "pm-100",
        "proposed_action": "rollback",
    }

    data, readback = await client.submit_proposal(
        proposal,
        idempotency_key="idmp-key-100",
        verify_readback=True,
    )

    assert data["decision_id"] == "dec-100"
    assert readback["decision_id"] == "dec-100"
    assert len(dummy_http.requests) == 2

    post_req = dummy_http.requests[0]
    assert post_req[1] == "http://evolution-test:8093/api/evolution/proposals"
    assert post_req[3]["Authorization"] == "Bearer secret-token-123"
    assert post_req[3]["X-Tenant-Id"] == "tenant-test-1"
    assert post_req[3]["X-Idempotency-Key"] == "idmp-key-100"

    get_req = dummy_http.requests[1]
    assert get_req[1] == "http://evolution-test:8093/api/evolution/proposals/dec-100"
    assert get_req[3]["Authorization"] == "Bearer secret-token-123"
    assert get_req[3]["X-Tenant-Id"] == "tenant-test-1"


@pytest.mark.anyio
async def test_evolution_client_401_negative():
    class DummyResponse:
        def __init__(self, status_code, text="Unauthorized"):
            self.status_code = status_code
            self.text = text

        def json(self):
            return {"detail": self.text}

    class DummyClient:
        async def post(self, url, json, headers):
            return DummyResponse(401, "invalid Evolution bearer token")

    dummy_http = DummyClient()
    client = EvolutionClient(
        base_url="http://evolution-test:8093",
        auth_token="wrong-token",
        tenant_id="tenant-test-1",
        async_client=dummy_http,
    )

    with pytest.raises(EvolutionAuthenticationError) as exc_info:
        await client.submit_proposal({"source_postmortem_id": "pm-100"}, verify_readback=False)

    assert exc_info.value.status_code == 401
    assert "401" in str(exc_info.value)


@pytest.mark.anyio
async def test_evolution_client_readback_mismatch():
    class DummyResponse:
        def __init__(self, status_code, json_data):
            self.status_code = status_code
            self._json_data = json_data
            self.text = ""

        def json(self):
            return self._json_data

    class DummyClient:
        async def post(self, url, json, headers):
            return DummyResponse(201, {"decision_id": "dec-100"})

        async def get(self, url, headers):
            # Returns a readback with mismatched source_postmortem_id
            return DummyResponse(
                200,
                {
                    "decision_id": "dec-100",
                    "source_postmortem_id": "pm-DIFFERENT",
                },
            )

    dummy_http = DummyClient()
    client = EvolutionClient(
        base_url="http://evolution-test:8093",
        auth_token="secret-token-123",
        tenant_id="tenant-test-1",
        async_client=dummy_http,
    )

    with pytest.raises(EvolutionReadbackError) as exc_info:
        await client.submit_proposal({"source_postmortem_id": "pm-100"}, verify_readback=True)

    assert "mismatch" in str(exc_info.value)


@pytest.mark.anyio
async def test_evolution_client_execute_program_action():
    captured_requests = []

    class MockHttpClient:
        async def post(self, url, json, headers):
            captured_requests.append({"url": url, "json": json, "headers": headers})
            return DummyResponse(200, {"receipt_id": "rcpt-1", "status": "active", "program_status": "active"})

    client = EvolutionClient(
        base_url="http://evolution-test:8093",
        auth_token="secret-token-123",
        tenant_id="tenant-test-1",
        async_client=MockHttpClient(),
    )

    result = await client.execute_program_action(
        "evp-123",
        "approve_program",
        actor_id="admin-1",
        actor_role="approver",
        expected_revision=3,
        idempotency_key="idemp-key-xyz",
        payload={"note": "Approval note"},
    )

    assert result["receipt_id"] == "rcpt-1"
    assert len(captured_requests) == 1
    req = captured_requests[0]
    assert req["url"] == "http://evolution-test:8093/api/evolution/programs/evp-123/actions/approve_program"
    assert req["headers"]["X-Tenant-Id"] == "tenant-test-1"
    assert req["headers"]["Authorization"] == "Bearer secret-token-123"
    assert req["headers"]["X-Idempotency-Key"] == "idemp-key-xyz"
    assert req["json"]["actor_id"] == "admin-1"
    assert req["json"]["actor_role"] == "approver"
    assert req["json"]["expected_revision"] == 3
    assert req["json"]["note"] == "Approval note"
