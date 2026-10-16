"""Real dev sessions reach the Workshop client without shared-token state."""
from __future__ import annotations

import asyncio
import copy
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
from services.control_plane.bff.agora.research.router import create_research_router
from services.control_plane.bff.agora.research.routes import plans as plans_route_mod
from services.control_plane.bff.agora.research.service import AgoraResearchService
from services.control_plane.bff.agora.research.store import MemoryResearchPlanStore
from services.control_plane.bff.tests.test_agora_workshop_live_operations import (
    BASE_REGISTRY_ID, STRATEGY_ID, FakeCanonicalOperations,
)


def workshop_app(app_factory, monkeypatch, *, reject_owner=False, expected_tokens=None):
    app = app_factory()
    client = operations.WorkshopCanonicalOperations(registry_base_url="http://registry.example.test")
    entry = FakeCanonicalOperations().registry[BASE_REGISTRY_ID]
    requests = []

    def owner(request, **_kwargs):
        assert request.full_url.startswith("http://registry.example.test/api/registry/strategy-specs/")
        authorization = request.get_header("Authorization")
        spec_key = request.full_url.rsplit("/", 1)[-1]
        if expected_tokens is not None:
            assert authorization == expected_tokens[spec_key]
        principal = policy.extract_identity(authorization)
        policy.require_read_role(principal)
        assert principal.claims["tenant_id"] == "tenant-dev"
        assert not principal.mfa_verified
        requests.append(request)
        time.sleep(0.01)  # Exercise overlapping sync endpoint threads.
        if reject_owner:
            raise urllib.error.HTTPError(request.full_url, 401, "rejected", {}, io.BytesIO(b'{}'))
        result_entry = copy.deepcopy(entry)
        result_entry["entry"]["registry_id"] = spec_key
        return io.BytesIO(json.dumps(result_entry).encode())

    monkeypatch.setattr(urllib.request, "urlopen", owner)
    workshop_store = MemoryWorkshopStore()
    app.include_router(create_strategy_workshop_router(
        extract_identity=policy.extract_identity, require_read_role=policy.require_read_role,
        require_write_role=policy.require_operator_role, bff_error=policy.bff_error,
        utc_now=policy.default_utc_now, workshop_store=workshop_store,
        canonical_operations=client,
    ))
    research_store = MemoryResearchPlanStore()
    research_service = AgoraResearchService(
        store=research_store,
        workshop_store=workshop_store,
        utc_now=policy.default_utc_now,
        bff_error=policy.bff_error,
        canonical_operations=client,
    )
    app.include_router(create_research_router(
        extract_identity=policy.extract_identity,
        require_read_role=policy.require_read_role,
        require_write_role=policy.require_operator_role,
        bff_error=policy.bff_error,
        utc_now=policy.default_utc_now,
        research_plan_store=research_store,
        workshop_store=workshop_store,
        service=research_service,
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


def test_research_plan_request_authorization_ordinary_and_servant(app_factory, monkeypatch):
    app, requests = workshop_app(app_factory, monkeypatch)
    monkeypatch.setattr(
        plans_route_mod,
        "draft_research_plan",
        lambda prompt, **_kwargs: {
            "spec_version": "1.0",
            "strategy_id": STRATEGY_ID,
            "strategy_spec_registry_id": BASE_REGISTRY_ID,
            "stages": [{"stage_type": "prototype_backtest"}],
        },
    )
    plan_body = {
        "spec_version": "1.0",
        "strategy_id": STRATEGY_ID,
        "strategy_spec_registry_id": BASE_REGISTRY_ID,
        "stages": [{"stage_type": "prototype_backtest"}],
    }

    with TestClient(app, base_url=BASE) as browser:
        # 1. Cookie login and workshop creation
        assert login(browser).status_code == 200
        cookie_token = browser.cookies.get("pantheon_session")
        created = browser.post(
            "/bff/agora/workshops",
            headers={"Origin": ORIGIN, "Idempotency-Key": "c324e7a2-33e7-4d65-888f-a8ed153bb7ac"},
            json={"initial_message": "Research only", "strategy_spec_ref": BASE_REGISTRY_ID},
        )
        assert created.status_code == 201, created.text
        workshop_id = created.json()["data"]["workshop_id"]

        # Ordinary draft plan with cookie session
        ordinary_res = browser.post(
            f"/bff/agora/workshops/{workshop_id}/research-plans",
            headers={"Origin": ORIGIN, "Idempotency-Key": "plan-cookie-1"},
            json=plan_body,
        )
        assert ordinary_res.status_code == 201, ordinary_res.text
        assert operations._request_authorization.get() is None

        # Servant draft plan with cookie session
        servant_res = browser.post(
            f"/bff/agora/workshops/{workshop_id}/research-plans/servant-proposal",
            headers={"Origin": ORIGIN, "Idempotency-Key": "servant-cookie-1"},
            json={"prompt": "Draft momentum plan"},
        )
        assert servant_res.status_code == 201, servant_res.text
        assert operations._request_authorization.get() is None

        # 2. Bearer token login (separate dev-login)
        login_res = browser.post("/bff/auth/dev-login", headers={"Origin": ORIGIN}, json=CREDENTIALS)
        assert login_res.status_code == 200, login_res.text
        bearer_token = login_res.json()["access_token"]

        ordinary_bearer = browser.post(
            f"/bff/agora/workshops/{workshop_id}/research-plans",
            headers={"Authorization": f"Bearer {bearer_token}", "Idempotency-Key": "plan-bearer-1"},
            json=plan_body,
        )
        assert ordinary_bearer.status_code == 201, ordinary_bearer.text
        assert operations._request_authorization.get() is None

        servant_bearer = browser.post(
            f"/bff/agora/workshops/{workshop_id}/research-plans/servant-proposal",
            headers={"Authorization": f"Bearer {bearer_token}", "Idempotency-Key": "servant-bearer-1"},
            json={"prompt": "Draft momentum plan bearer"},
        )
        assert servant_bearer.status_code == 201, servant_bearer.text
        assert operations._request_authorization.get() is None

        # 3. Anonymous denial
        with TestClient(app, base_url=BASE) as unauth_browser:
            anon_ord = unauth_browser.post(
                f"/bff/agora/workshops/{workshop_id}/research-plans",
                headers={"Idempotency-Key": "plan-anon-1"},
                json=plan_body,
            )
            assert anon_ord.status_code == 401
            anon_serv = unauth_browser.post(
                f"/bff/agora/workshops/{workshop_id}/research-plans/servant-proposal",
                headers={"Idempotency-Key": "servant-anon-1"},
                json={"prompt": "Anonymous prompt"},
            )
            assert anon_serv.status_code == 401
            assert operations._request_authorization.get() is None

        # 4. Foreign tenant rejection
        foreign_ord = browser.post(
            f"/bff/agora/workshops/{workshop_id}/research-plans",
            headers={
                "Authorization": f"Bearer {bearer_token}",
                "X-Tenant-Id": "foreign-tenant",
                "Idempotency-Key": "plan-foreign-1",
            },
            json=plan_body,
        )
        assert foreign_ord.status_code in (403, 409), foreign_ord.text
        foreign_serv = browser.post(
            f"/bff/agora/workshops/{workshop_id}/research-plans/servant-proposal",
            headers={
                "Authorization": f"Bearer {bearer_token}",
                "X-Tenant-Id": "foreign-tenant",
                "Idempotency-Key": "servant-foreign-1",
            },
            json={"prompt": "Foreign prompt"},
        )
        assert foreign_serv.status_code in (403, 409), foreign_serv.text
        assert operations._request_authorization.get() is None

    # Verify requests reaching the simulated protected Registry
    assert len(requests) == 5
    assert requests[0].get_header("Authorization") == f"Bearer {cookie_token}"
    assert requests[1].get_header("Authorization") == f"Bearer {cookie_token}"
    assert requests[2].get_header("Authorization") == f"Bearer {cookie_token}"
    assert requests[3].get_header("Authorization") == f"Bearer {bearer_token}"
    assert requests[4].get_header("Authorization") == f"Bearer {bearer_token}"
    assert operations._request_authorization.get() is None


def test_research_plan_parallel_requests_and_failure_do_not_retain_callers_token(app_factory, monkeypatch):
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

    monkeypatch.setattr(
        plans_route_mod,
        "draft_research_plan",
        lambda prompt, **_kwargs: {
            "spec_version": "1.0",
            "strategy_id": STRATEGY_ID,
            "strategy_spec_registry_id": prompt,
            "stages": [{"stage_type": "prototype_backtest"}],
        },
    )

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as browser:
            async def request(index):
                reg_id = f"{BASE_REGISTRY_ID}-{index}"
                if index % 2 == 0:
                    result = await browser.post(
                        "/bff/agora/workshops/ws-parallel/research-plans",
                        headers={
                            "Authorization": "Bearer " + tokens[index % 2],
                            "Idempotency-Key": f"plan-key-{index}",
                        },
                        json={
                            "spec_version": "1.0",
                            "strategy_id": STRATEGY_ID,
                            "strategy_spec_registry_id": reg_id,
                            "stages": [{"stage_type": "prototype_backtest"}],
                        },
                    )
                else:
                    result = await browser.post(
                        "/bff/agora/workshops/ws-parallel/research-plans/servant-proposal",
                        headers={
                            "Authorization": "Bearer " + tokens[index % 2],
                            "Idempotency-Key": f"servant-key-{index}",
                        },
                        json={"prompt": reg_id},
                    )
                assert result.status_code == 503, result.text
                assert operations._request_authorization.get() is None

            await asyncio.gather(*(request(index) for index in range(12)))
            anonymous = await browser.post(
                "/bff/agora/workshops/ws-parallel/research-plans",
                headers={"Idempotency-Key": "plan-anon-key"},
                json={
                    "spec_version": "1.0",
                    "strategy_id": STRATEGY_ID,
                    "strategy_spec_registry_id": BASE_REGISTRY_ID,
                    "stages": [{"stage_type": "prototype_backtest"}],
                },
            )
            assert anonymous.status_code == 401
            assert operations._request_authorization.get() is None

    asyncio.run(run())
    assert len(requests) == 12
    assert {request.get_header("Authorization") for request in requests} == {f"Bearer {token}" for token in tokens}
    assert operations._request_authorization.get() is None


def test_research_plan_parallel_success_requests_do_not_exchange_credentials(app_factory, monkeypatch):
    expected_tokens = {}
    app, requests = workshop_app(app_factory, monkeypatch, reject_owner=False, expected_tokens=expected_tokens)
    tokens = []
    with TestClient(app, base_url=BASE) as browser:
        for _ in range(2):
            result = browser.post("/bff/auth/dev-login", json=CREDENTIALS)
            assert result.status_code == 200, result.text
            tokens.append(result.json()["access_token"])
    assert tokens[0] != tokens[1]
    for index in range(12):
        expected_tokens[f"{BASE_REGISTRY_ID}-{index}"] = "Bearer " + tokens[index % 2]

    monkeypatch.setattr(
        plans_route_mod,
        "draft_research_plan",
        lambda prompt, **_kwargs: {
            "spec_version": "1.0",
            "strategy_id": STRATEGY_ID,
            "strategy_spec_registry_id": prompt,
            "stages": [{"stage_type": "prototype_backtest"}],
        },
    )

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as browser:
            async def request(index):
                reg_id = f"{BASE_REGISTRY_ID}-{index}"
                if index % 2 == 0:
                    result = await browser.post(
                        "/bff/agora/workshops/ws-success/research-plans",
                        headers={
                            "Authorization": "Bearer " + tokens[index % 2],
                            "Idempotency-Key": f"plan-success-key-{index}",
                        },
                        json={
                            "spec_version": "1.0",
                            "strategy_id": STRATEGY_ID,
                            "strategy_spec_registry_id": reg_id,
                            "stages": [{"stage_type": "prototype_backtest"}],
                        },
                    )
                else:
                    result = await browser.post(
                        "/bff/agora/workshops/ws-success/research-plans/servant-proposal",
                        headers={
                            "Authorization": "Bearer " + tokens[index % 2],
                            "Idempotency-Key": f"servant-success-key-{index}",
                        },
                        json={"prompt": reg_id},
                    )
                assert result.status_code == 201, result.text
                assert operations._request_authorization.get() is None

            await asyncio.gather(*(request(index) for index in range(12)))

    asyncio.run(run())
    assert len(requests) == 12
    assert {request.get_header("Authorization") for request in requests} == {f"Bearer {token}" for token in tokens}
    assert operations._request_authorization.get() is None
