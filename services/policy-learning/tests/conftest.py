"""Shared inbound-authority fixtures for the policy-learning service tests.

L12-IMIT-001 made the imitation-loop routes authenticated and tenant-bound, so
every HTTP test needs a verified caller and an ``X-Tenant-Id``.  These helpers
configure the in-cluster service-token path, which is the same path the
scheduler sidecar uses in compose.
"""

from __future__ import annotations

import os
import urllib.request
from typing import Any
from unittest import mock

import pytest


TEST_SERVICE_TOKEN = "l12-imit-001-test-service-token"
TEST_SERVICE_TENANTS = "tenant-a,tenant-b,tenant-c,tenant-z,tenant-admit"


def auth_headers(tenant_id: str = "tenant-a", *, token: str = TEST_SERVICE_TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "X-Tenant-Id": tenant_id}


def authorized_client(app: Any, tenant_id: str = "tenant-a", *, token: str = TEST_SERVICE_TOKEN):
    from fastapi.testclient import TestClient

    return TestClient(app, headers=auth_headers(tenant_id, token=token))


@pytest.fixture(autouse=True)
def policy_learning_service_auth():
    """Give the whole module a configured service token and tenant authority."""

    with mock.patch.dict(
        os.environ,
        {
            "POLICY_LEARNING_SERVICE_TOKEN": TEST_SERVICE_TOKEN,
            "POLICY_LEARNING_SERVICE_TENANTS": TEST_SERVICE_TENANTS,
        },
    ):
        yield


def mock_research_http_urlopen(req, timeout=None):
    """Mock urllib.request.urlopen for Research HTTP intake & readback endpoints."""
    url = req.full_url
    method = req.get_method()
    if "/api/research-orchestrator/" in url:
        from fastapi.testclient import TestClient
        from services.research.main import app as res_app

        res_client = TestClient(res_app)
        headers = dict(req.headers)
        if method == "POST" and "/api/research-orchestrator/intake/imitation-candidate" in url:
            payload = req.data
            response = res_client.post("/api/research-orchestrator/intake/imitation-candidate", content=payload, headers=headers)
            class MockResp:
                status = response.status_code
                def read(self):
                    return response.content
                def __enter__(self):
                    return self
                def __exit__(self, *args):
                    pass
            return MockResp()
        elif method == "GET" and "/api/research-orchestrator/runs/" in url:
            run_id = url.split("/api/research-orchestrator/runs/")[1]
            response = res_client.get(f"/api/research-orchestrator/runs/{run_id}", headers=headers)
            class MockResp:
                status = response.status_code
                def read(self):
                    return response.content
                def __enter__(self):
                    return self
                def __exit__(self, *args):
                    pass
            return MockResp()

    # Pass through original urlopen for non-research-orchestrator requests
    return _orig_urlopen(req, timeout=timeout)


_orig_urlopen = urllib.request.urlopen


@pytest.fixture(autouse=True)
def mock_research_http_intake_autouse(request):
    """Autouse fixture to mock Research HTTP intake endpoint unless test explicitly manages urlopen."""
    if "no_autouse_research_http_mock" in request.keywords:
        yield
        return

    with mock.patch("urllib.request.urlopen", side_effect=mock_research_http_urlopen):
        yield
