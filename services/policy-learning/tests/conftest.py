"""Shared inbound-authority fixtures for the policy-learning service tests.

L12-IMIT-001 made the imitation-loop routes authenticated and tenant-bound, so
every HTTP test needs a verified caller and an ``X-Tenant-Id``.  These helpers
configure the in-cluster service-token path, which is the same path the
scheduler sidecar uses in compose.
"""

from __future__ import annotations

import os
from typing import Any
from unittest import mock

import pytest


TEST_SERVICE_TOKEN = "l12-imit-001-test-service-token"
TEST_SERVICE_TENANTS = "tenant-a,tenant-b,tenant-c,tenant-z"


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
