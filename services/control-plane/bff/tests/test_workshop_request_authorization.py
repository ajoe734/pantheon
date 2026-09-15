"""Real dev sessions reach the Workshop client without shared-token state."""
from __future__ import annotations

import asyncio
import io
import json
import time
import urllib.error
import urllib.request

import httpx
from fastapi.testclient import TestClient

from services.control_plane.bff.auth import policy
from services.control_plane.bff.auth.test_browser_session import (  # noqa: F401
    BASE, CREDENTIALS, ORIGIN, app_factory, login,
)
from services.control_plane.bff.agora.strategy_workshop import operations
from services.control_plane.bff.agora.strategy_workshop.router import create_strategy_workshop_router
from services.control_plane.bff.agora.strategy_workshop.store import MemoryWorkshopStore
from services.control_plane.bff.tests.test_agora_workshop_live_operations import (
    BASE_REGISTRY_ID, FakeCanonicalOperations,
)


def workshop_app(app_factory, monkeypatch, *, reject_owner=False, expected_tokens=None):
    app = app_factory()
    client = operations.WorkshopCanonicalOperations(registry_base_url="http://registry.example.test")
    entry = FakeCanonicalOperations().registry[BASE_REGISTRY_ID]
    requests = []

    def owner(request, **_kwargs):
        assert request.full_url.startswith("http://registry.example.test/api/registry/strategy-specs/")
        authorization = request.get_header("Authorization")
        if expected_tokens is not None:
            assert authorization == expected_tokens[request.full_url.rsplit("/", 1)[-1]]
        principal = policy.extract_identity(authorization)
        policy.require_read_role(principal)
        assert principal.claims["tenant_id"] == "tenant-dev"
        assert not principal.mfa_verified
        requests.append(request)
        time.sleep(0.01)  # Exercise overlapping sync endpoint threads.
        if reject_owner:
            raise urllib.error.HTTPError(request.full_url, 401, "rejected", {}, io.BytesIO(b'{}'))
        return io.BytesIO(json.dumps(entry).encode())

    monkeypatch.setattr(urllib.request, "urlopen", owner)
    app.include_router(create_strategy_workshop_router(
        extract_identity=policy.extract_identity, require_read_role=policy.require_read_role,
        require_write_role=policy.require_operator_role, bff_error=policy.bff_error,
        utc_now=policy.default_utc_now, workshop_store=MemoryWorkshopStore(),
        canonical_operations=client,
    ))
    return app, requests


def test_real_cookie_login_reaches_registry_and_version_reload(app_factory, monkeypatch):
    app, requests = workshop_app(app_factory, monkeypatch)
    with TestClient(app, base_url=BASE) as browser:
        assert login(browser).status_code == 200
        token = browser.cookies.get("pantheon_session")
        created = browser.post("/bff/agora/workshops", headers={
            "Origin": ORIGIN, "Idempotency-Key": "c324e7a2-33e7-4d65-888f-a8ed153bb7ac",
        }, json={
            "initial_message": "Research only", "strategy_spec_ref": BASE_REGISTRY_ID,
        })
        assert created.status_code == 201, created.text
        workshop_id = created.json()["data"]["workshop_id"]
        for _ in range(2):
            readback = browser.get(f"/bff/agora/workshops/{workshop_id}/versions")
            assert readback.status_code == 200, readback.text
    assert len(requests) >= 3
    assert all(request.get_header("Authorization") == f"Bearer {token}" for request in requests)
    assert operations._request_authorization.get() is None
    redirected = urllib.request.HTTPRedirectHandler().redirect_request(
        requests[0], None, 302, "redirect", {}, "https://unrelated.example.test/",
    )
    assert redirected.get_header("Authorization") is None


def test_parallel_requests_and_failure_do_not_retain_another_callers_token(app_factory, monkeypatch):
    expected_tokens = {}
    app, requests = workshop_app(app_factory, monkeypatch, reject_owner=True, expected_tokens=expected_tokens)
    tokens = []
    with TestClient(app, base_url=BASE) as browser:
        for _ in range(2):
            result = browser.post("/bff/auth/dev-login", json=CREDENTIALS)
            assert result.status_code == 200, result.text
            tokens.append(result.json()["access_token"])
    assert tokens[0] != tokens[1]
    for index in range(12):
        expected_tokens[f"{BASE_REGISTRY_ID}-{index}"] = "Bearer " + tokens[index % 2]

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as browser:
            async def request(index):
                result = await browser.post("/bff/agora/workshops", headers={
                    "Authorization": "Bearer " + tokens[index % 2],
                }, json={"initial_message": "Research only", "strategy_spec_ref": f"{BASE_REGISTRY_ID}-{index}"})
                assert result.status_code == 502, result.text
                assert operations._request_authorization.get() is None
            await asyncio.gather(*(request(index) for index in range(12)))
            anonymous = await browser.post("/bff/agora/workshops", json={
                "initial_message": "Research only", "strategy_spec_ref": BASE_REGISTRY_ID,
            })
            assert anonymous.status_code == 401
    asyncio.run(run())
    assert len(requests) == 12
    assert {request.get_header("Authorization") for request in requests} == {f"Bearer {token}" for token in tokens}
    assert operations._request_authorization.get() is None
