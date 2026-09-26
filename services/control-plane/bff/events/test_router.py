"""Tests for BFF Events domain router (ACG-01-004, ACG-01-005)."""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional
from fastapi import FastAPI
from fastapi.testclient import TestClient
import os
import sys


from services.control_plane.bff.events.router import create_events_router


def _make_mock_read_store(events_list: Optional[List[Dict[str, Any]]] = None, status: str = "ok"):
    class MockReadStore:
        def __init__(self):
            self.events = list(events_list or [])
            self.status = status

        def list_governance_audit_events(
            self,
            actor: Optional[str] = None,
            action_types: Optional[List[str]] = None,
            target_type: Optional[str] = None,
            **kwargs,
        ) -> List[Dict[str, Any]]:
            res = self.events
            if actor:
                res = [e for e in res if e.get("actor") == actor]
            if action_types:
                res = [e for e in res if e.get("action_type") in action_types or e.get("type") in action_types]
            if target_type:
                res = [e for e in res if e.get("target_type") == target_type]
            return res

        def dataset_source(self, ds: str) -> str:
            return self.status

    return MockReadStore()


def test_events_router_routes_uniqueness():
    router = create_events_router()
    routes = [(getattr(r, "methods", set()), getattr(r, "path", "")) for r in router.routes]
    events_get_routes = [r for r in routes if r[1] == "/bff/events" and "GET" in r[0]]
    stream_get_routes = [r for r in routes if r[1] == "/bff/events/stream" and "GET" in r[0]]

    assert len(events_get_routes) == 1
    assert len(stream_get_routes) == 1
    assert len(router.routes) == 14


def test_events_router_list_events_and_filtering():
    sample_events = [
        {"id": "e1", "action_type": "ApproveDeployment", "actor": "op-1", "target_type": "deployment"},
        {"id": "e2", "action_type": "RejectDeployment", "actor": "op-2", "target_type": "deployment"},
        {"id": "e3", "action_type": "CreateStrategy", "actor": "op-1", "target_type": "strategy"},
    ]
    store = _make_mock_read_store(sample_events)
    router = create_events_router(get_read_store=lambda: store)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    # 1. List all
    resp = client.get("/bff/events", headers={"Authorization": "Bearer op-1:operator"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 3
    assert "meta" in data
    assert "page_info" in data

    # 2. Filter by event_type
    resp = client.get("/bff/events?event_type=ApproveDeployment", headers={"Authorization": "Bearer op-1:operator"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 1
    assert data["items"][0]["id"] == "e1"

    # 3. Filter by actor
    resp = client.get("/bff/events?actor=op-2", headers={"Authorization": "Bearer op-1:operator"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 1
    assert data["items"][0]["id"] == "e2"

    # 4. Filter by target_type
    resp = client.get("/bff/events?target_type=strategy", headers={"Authorization": "Bearer op-1:operator"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 1
    assert data["items"][0]["id"] == "e3"


def test_events_router_list_degraded_when_unavailable():
    sample_events = [{"id": "e1", "action_type": "ApproveDeployment"}]
    store = _make_mock_read_store(sample_events, status="unavailable")
    router = create_events_router(
        get_read_store=lambda: store,
        dataset_surface_status=lambda ds, **kw: {"status": "unavailable", "source": "missing"},
    )
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    resp = client.get("/bff/events", headers={"Authorization": "Bearer op-1:operator"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["items"] == []


def test_events_router_stream_unauthenticated_liveness():
    async def _test_frontend_stream(channels):
        yield "id: evt-1\ndata: {\"channels\": [\"system\"]}\n\n"

    router = create_events_router(frontend_bff_event_stream=_test_frontend_stream)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    resp = client.get("/bff/events/stream")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert resp.headers["x-sse-channel"] == "bff"
    assert resp.headers["x-sse-replay-supported"] == "false"


def test_events_router_stream_authenticated_channel():
    from collections import deque
    from fastapi.sse import EventSourceResponse

    async def _test_gen():
        yield "id: evt-1\nevent: message\ndata: {}\n\n"

    class MockEventStream:
        channels = ("governance",)
        buffers = {"governance": deque()}
        subscribers = {"governance": []}

        def stream_response(self, channel, last_event_id, **kwargs):
            headers = {
                "X-SSE-Channel": channel,
                "X-SSE-Replay-Supported": "true",
            }
            if kwargs.get("extra_headers"):
                headers.update(kwargs["extra_headers"])
            return EventSourceResponse(_test_gen(), headers=headers)

    router = create_events_router(event_stream_service=MockEventStream())
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    with client.stream(
        "GET",
        "/bff/events/stream?channel=governance",
        headers={"Authorization": "Bearer op-1:operator"},
    ) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        assert resp.headers["x-sse-channel"] == "governance"
        assert resp.headers["x-sse-replay-supported"] == "true"


def test_events_router_stream_invalid_channel():
    router = create_events_router()
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    resp = client.get(
        "/bff/events/stream?channel=invalid_unknown_channel",
        headers={"Authorization": "Bearer op-1:operator"},
    )
    assert resp.status_code == 400
    data = resp.json()
    err = data["error"] if "error" in data else data.get("detail", {}).get("error", {})
    assert err.get("code") == "VALIDATION_FAILED"


def test_events_router_native_sse_routes_flag():
    router = create_events_router()
    sse_paths = {
        "/bff/events/stream",
        "/api/v1/stream/{channel}",
        "/bff/sse/notifications",
        "/bff/sse/command-center/kpi",
        "/bff/sse/command-center/events",
        "/bff/sse/jobs/{jobId}/progress",
        "/bff/sse/alerts",
        "/bff/sse/incidents/{incidentId}/timeline",
        "/bff/sse/deployment/events",
        "/bff/sse/agora/signals",
        "/bff/sse/agora/sessions/{sessionId}",
        "/bff/sse/review/updates",
    }
    for route in router.routes:
        if getattr(route, "path", None) in sse_paths:
            assert getattr(route, "is_sse_stream", False) is True, f"Route {route.path} must have is_sse_stream == True"


def _make_finite_service(service: EventStreamService, limit: int = 10):
    original_stream = service.stream

    async def finite_stream(*args, **kwargs):
        stream = original_stream(*args, **kwargs)
        try:
            for _ in range(limit):
                yield await asyncio.wait_for(anext(stream), 0.1)
        except (TimeoutError, asyncio.TimeoutError):
            pass
        finally:
            await stream.aclose()

    service.stream = finite_stream
    return service


def test_events_router_tenant_isolation_in_memory_and_file_replay(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from events.service import EventStreamService

    # 1. In-memory tenant isolation test
    service = EventStreamService(channels=("approval", "tool"))
    for tenant in ("tenant-a", "tenant-b"):
        service.publish(
            service.buffers["approval"],
            service.subscribers["approval"],
            "approval.created",
            {"tenant_id": tenant, "secret": tenant + "-private-payload"},
        )
    _make_finite_service(service)

    def extract_tenant_a(*args, **kwargs):
        return SimpleNamespace(operator_id="alice", tenant_id="tenant-a", roles={"viewer"}, is_authenticated=True)

    router = create_events_router(
        event_stream_service=service,
        extract_identity=extract_tenant_a,
        require_read_role=lambda ident: None,
    )
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    resp = client.get("/api/v1/stream/approval", headers={"Authorization": "Bearer token"})
    assert resp.status_code == 200
    assert "tenant-a-private-payload" in resp.text
    assert "tenant-b-private-payload" not in resp.text

    # 2. File-based tenant isolation test
    monkeypatch.setenv("PANTHEON_BFF_SSE_REPLAY_STORE", "file")
    file_service = EventStreamService(channels=("approval", "tool"), data_dir=str(tmp_path))
    for tenant in ("tenant-a", "tenant-b"):
        file_service.publish(
            file_service.buffers["approval"],
            file_service.subscribers["approval"],
            "approval.created",
            {"tenant_id": tenant, "secret": tenant + "-file-private-payload"},
        )
    _make_finite_service(file_service)

    router_file = create_events_router(
        event_stream_service=file_service,
        extract_identity=extract_tenant_a,
        require_read_role=lambda ident: None,
    )
    app_file = FastAPI()
    app_file.include_router(router_file)
    client_file = TestClient(app_file)

    resp_file = client_file.get("/api/v1/stream/approval", headers={"Authorization": "Bearer token"})
    assert resp_file.status_code == 200
    assert "tenant-a-file-private-payload" in resp_file.text
    assert "tenant-b-file-private-payload" not in resp_file.text


def test_events_router_cursor_handling_and_409_conflict():
    from types import SimpleNamespace
    from events.service import EventStreamService

    service = EventStreamService(channels=("approval",))
    id_1 = service.publish(
        service.buffers["approval"],
        service.subscribers["approval"],
        "approval.created",
        {"tenant_id": "tenant-a", "seq": 1},
    )
    id_2 = service.publish(
        service.buffers["approval"],
        service.subscribers["approval"],
        "approval.created",
        {"tenant_id": "tenant-a", "seq": 2},
    )
    _make_finite_service(service)

    router = create_events_router(
        event_stream_service=service,
        extract_identity=lambda *a, **k: SimpleNamespace(operator_id="alice", tenant_id="tenant-a", roles={"viewer"}, is_authenticated=True),
        require_read_role=lambda ident: None,
    )
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    # 1. Last-Event-ID header cursor excludes cursor event from replay
    resp = client.get(
        "/api/v1/stream/approval",
        headers={"Authorization": "Bearer token", "Last-Event-ID": id_1},
    )
    assert resp.status_code == 200
    assert f"id: {id_1}\n" not in resp.text
    assert f"id: {id_2}\n" in resp.text

    # 2. Unknown cursor returns HTTP 409 with replay headers
    resp_missing = client.get(
        "/api/v1/stream/approval",
        headers={"Authorization": "Bearer token", "Last-Event-ID": "unknown-cursor-xyz"},
    )
    assert resp_missing.status_code == 409
    assert resp_missing.headers.get("X-SSE-Replay-Supported") == "true"
    assert resp_missing.headers.get("X-SSE-Channel") == "approval"

    # 3. bff/events/stream honors the same Last-Event-ID header and returns 409 for unknown
    resp_bff_missing = client.get(
        "/bff/events/stream?channel=approval",
        headers={"Authorization": "Bearer token", "Last-Event-ID": "unknown-cursor-xyz"},
    )
    assert resp_bff_missing.status_code == 409
    assert resp_bff_missing.headers.get("X-SSE-Replay-Supported") == "true"

    resp_bff_known = client.get(
        "/bff/events/stream?channel=approval",
        headers={"Authorization": "Bearer token", "Last-Event-ID": id_1},
    )
    assert resp_bff_known.status_code == 200
    assert f"id: {id_1}\n" not in resp_bff_known.text
    assert f"id: {id_2}\n" in resp_bff_known.text


def test_events_router_real_operator_identity_jwt_and_cookie_tenant_isolation(monkeypatch, tmp_path):
    from services.control_plane.bff.auth import policy
    from services.control_plane.bff.auth.test_policy import (
        _make_jwt,
        TEST_JWT_SECRET,
        TEST_JWT_ISSUER,
        TEST_JWT_AUDIENCE,
    )
    from events.service import EventStreamService

    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "false")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_BFF_JWT_SECRET", TEST_JWT_SECRET)
    monkeypatch.setenv("PANTHEON_BFF_JWT_ISSUER", TEST_JWT_ISSUER)
    monkeypatch.setenv("PANTHEON_BFF_JWT_AUDIENCE", TEST_JWT_AUDIENCE)

    token_a = _make_jwt(
        subject="alice",
        roles=["viewer"],
        extra={"tenant_id": "tenant-a", "allowed_tenants": ["tenant-a"]},
    )
    token_b = _make_jwt(
        subject="bob",
        roles=["viewer"],
        extra={"tenant_id": "tenant-b", "allowed_tenants": ["tenant-b"]},
    )

    ident_a = policy.extract_identity("Bearer " + token_a)
    assert ident_a.claims.get("tenant_id") == "tenant-a"

    for mode in ("memory", "file"):
        monkeypatch.setenv("PANTHEON_BFF_SSE_REPLAY_STORE", mode)
        store_dir = tmp_path / f"store-{mode}"
        store_dir.mkdir(parents=True, exist_ok=True)
        service = EventStreamService(channels=("approval",), data_dir=str(store_dir))
        for t in ("tenant-a", "tenant-b"):
            service.publish(
                service.buffers["approval"],
                service.subscribers["approval"],
                "approval.created",
                {"tenant_id": t, "secret": f"{t}-{mode}-secret-data"},
            )
        _make_finite_service(service)

        router = create_events_router(
            event_stream_service=service,
            extract_identity=policy.extract_identity,
            require_read_role=policy.require_read_role,
        )
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        # 1. Bearer token in Authorization header
        for route in (
            "/api/v1/stream/approval",
            "/bff/events/stream?channel=approval",
            "/bff/sse/review/updates",
        ):
            resp = client.get(route, headers={"Authorization": f"Bearer {token_a}"})
            assert resp.status_code == 200, f"Failed on route {route} in mode {mode}"
            assert resp.headers.get("X-Tenant-Id") == "tenant-a"
            assert f"tenant-a-{mode}-secret-data" in resp.text
            assert f"tenant-b-{mode}-secret-data" not in resp.text

        # 2. Cookie 'pantheon_session' without Authorization header
        cookie_client = TestClient(app)
        cookie_client.cookies.set("pantheon_session", token_a)
        for route in (
            "/api/v1/stream/approval",
            "/bff/events/stream?channel=approval",
            "/bff/sse/review/updates",
        ):
            resp_cookie = cookie_client.get(route)
            assert resp_cookie.status_code == 200, f"Failed cookie on route {route} in mode {mode}"
            assert resp_cookie.headers.get("X-Tenant-Id") == "tenant-a"
            assert f"tenant-a-{mode}-secret-data" in resp_cookie.text
            assert f"tenant-b-{mode}-secret-data" not in resp_cookie.text


def test_events_router_tenant_scoping_forbidden_for_unauthorized_tenant(monkeypatch, tmp_path):
    from services.control_plane.bff.auth import policy
    from services.control_plane.bff.auth.test_policy import (
        _make_jwt,
        TEST_JWT_SECRET,
        TEST_JWT_ISSUER,
        TEST_JWT_AUDIENCE,
    )
    from events.service import EventStreamService

    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "false")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_BFF_JWT_SECRET", TEST_JWT_SECRET)
    monkeypatch.setenv("PANTHEON_BFF_JWT_ISSUER", TEST_JWT_ISSUER)
    monkeypatch.setenv("PANTHEON_BFF_JWT_AUDIENCE", TEST_JWT_AUDIENCE)

    token = _make_jwt(
        subject="alice",
        roles=["viewer"],
        extra={"tenant_id": "tenant-a", "allowed_tenants": ["tenant-a"]},
    )
    service = EventStreamService(channels=("approval",), data_dir=str(tmp_path))
    _make_finite_service(service)

    router = create_events_router(
        event_stream_service=service,
        extract_identity=policy.extract_identity,
        require_read_role=policy.require_read_role,
    )
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    # Requesting unauthorized tenant via X-Tenant-Id header fails with 403
    resp_header = client.get(
        "/api/v1/stream/approval",
        headers={"Authorization": f"Bearer {token}", "X-Tenant-Id": "tenant-b"},
    )
    assert resp_header.status_code == 403
    assert "tenant_scope" in resp_header.text

    # Requesting unauthorized tenant via query param fails with 403
    resp_query = client.get(
        "/bff/events/stream?channel=approval&tenant_id=tenant-b",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp_query.status_code == 403
    assert "tenant_scope" in resp_query.text


def test_events_router_live_events_tenant_filtering(monkeypatch, tmp_path):
    from services.control_plane.bff.auth import policy
    from services.control_plane.bff.auth.test_policy import (
        _make_jwt,
        TEST_JWT_SECRET,
        TEST_JWT_ISSUER,
        TEST_JWT_AUDIENCE,
    )
    from events.service import EventStreamService

    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "false")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_BFF_JWT_SECRET", TEST_JWT_SECRET)
    monkeypatch.setenv("PANTHEON_BFF_JWT_ISSUER", TEST_JWT_ISSUER)
    monkeypatch.setenv("PANTHEON_BFF_JWT_AUDIENCE", TEST_JWT_AUDIENCE)

    token_a = _make_jwt(
        subject="alice",
        roles=["viewer"],
        extra={"tenant_id": "tenant-a", "allowed_tenants": ["tenant-a"]},
    )
    service = EventStreamService(channels=("approval",), data_dir=str(tmp_path))
    # Publish initial replay events
    service.publish(
        service.buffers["approval"],
        service.subscribers["approval"],
        "approval.created",
        {"tenant_id": "tenant-a", "msg": "replayed-a"},
    )
    service.publish(
        service.buffers["approval"],
        service.subscribers["approval"],
        "approval.created",
        {"tenant_id": "tenant-b", "msg": "replayed-b"},
    )
    orig_stream = service.stream
    async def live_stream(*args, **kwargs):
        service.publish(
            service.buffers["approval"],
            service.subscribers["approval"],
            "approval.created",
            {"tenant_id": "tenant-b", "msg": "live-b"},
        )
        service.publish(
            service.buffers["approval"],
            service.subscribers["approval"],
            "approval.created",
            {"tenant_id": "tenant-a", "msg": "live-a"},
        )
        s = orig_stream(*args, **kwargs)
        try:
            for _ in range(5):
                yield await asyncio.wait_for(anext(s), 0.1)
        except (TimeoutError, StopAsyncIteration):
            pass
        finally:
            await s.aclose()
    service.stream = live_stream

    router = create_events_router(
        event_stream_service=service,
        extract_identity=policy.extract_identity,
        require_read_role=policy.require_read_role,
    )
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    resp = client.get("/bff/sse/review/updates", headers={"Authorization": f"Bearer {token_a}"})
    assert resp.status_code == 200
    assert "replayed-a" in resp.text
    assert "live-a" in resp.text
    assert "replayed-b" not in resp.text
    assert "live-b" not in resp.text


