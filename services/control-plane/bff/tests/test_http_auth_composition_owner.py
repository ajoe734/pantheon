"""BFF-HTTP-AUTH-COMPOSITION-SEAM-CORRECTIVE-001: HTTP/Auth single-owner composition tests.

Verifies:
1. Single-owner composition of core/app_factory, core/errors, core/http_security, and core/lifespan.
2. JWKS prewarm: direct URI, discovery, no-config, failure-swallowing, cache-hit, and fail-closed auth.
3. Single ProviderReadinessCache instance shared by AuthFacadeService and lifespan, single background refresh task, and clean cancellation/shutdown.
4. CORS preflight 204, security headers on streaming responses, and 401/403/422/500 Pack D error envelopes with CORS preserved.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import importlib
import json
import os
import sys
from pathlib import Path
from typing import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient
from pydantic import BaseModel

BFF_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[4]
if str(BFF_DIR) not in sys.path:
    sys.path.insert(0, str(BFF_DIR))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.control_plane.bff import main as bff_main
from services.control_plane.bff.core.app_factory import build_bff_app
from services.control_plane.bff.core.errors import register_error_handlers
from services.control_plane.bff.core.http_security import (
    _PantheonCORSMiddleware,
    _SecurityHeadersMiddleware,
    _cors_origin_allowed,
    _cors_origins_from_env,
)
from services.control_plane.bff.core.lifespan import _prewarm_jwks_cache, create_lifespan, refresh_provider_readiness
from services.control_plane.bff.auth.service import ProviderReadinessCache, AuthFacadeService
from services.control_plane.bff.auth.policy import require_admin_mfa
from services.control_plane.bff.auth.browser_session import DevBrowserSessionMiddleware
from services.control_plane.bff.models import OperatorIdentity


class TestSingleOwnerComposition:
    """Verifies that app factory, lifespan, and auth facade share a single owner structure."""

    def test_main_app_composition_and_shared_cache(self) -> None:
        """bff_main.app and auth_facade share the same ProviderReadinessCache instance."""
        assert bff_main.app is not None
        assert isinstance(bff_main.provider_readiness_cache, ProviderReadinessCache)
        assert bff_main.auth_facade_service.provider_readiness_cache is bff_main.provider_readiness_cache

    def test_build_bff_app_wires_middlewares_and_handlers(self) -> None:
        """build_bff_app configures CORS, Security headers, and Pack D error handlers."""
        app = build_bff_app(
            title="Custom Test BFF",
            version="1.0.0-test",
            origin_allowed=lambda origin: origin == "https://test.example.com",
        )
        assert app.title == "Custom Test BFF"
        assert app.version == "1.0.0-test"

        # Check middleware stack contains security headers and CORS
        middleware_classes = [m.cls for m in app.user_middleware]
        assert _SecurityHeadersMiddleware in middleware_classes
        assert _PantheonCORSMiddleware in middleware_classes
        assert DevBrowserSessionMiddleware not in middleware_classes

    def test_build_bff_app_wires_session_middleware_only_when_configured(self) -> None:
        """DevBrowserSessionMiddleware is wired only when both dev_login_enabled and validate_session are passed."""
        app_with_session = build_bff_app(
            title="Session Test BFF",
            dev_login_enabled=lambda: True,
            validate_session=lambda token: None,
        )
        classes = [m.cls for m in app_with_session.user_middleware]
        assert DevBrowserSessionMiddleware in classes

        app_no_session = build_bff_app(
            title="No Session Test BFF",
            dev_login_enabled=lambda: True,
            validate_session=None,
        )
        classes_no_session = [m.cls for m in app_no_session.user_middleware]
        assert DevBrowserSessionMiddleware not in classes_no_session

    def test_require_admin_mfa_policy_ownership(self) -> None:
        """require_admin_mfa is owned by auth/policy and referenced by main."""
        assert bff_main._require_admin_mfa is require_admin_mfa

        admin_mfa_identity = OperatorIdentity(
            operator_id="admin1",
            roles=["admin", "operator"],
            mfa_verified=True,
        )
        # Should not raise
        require_admin_mfa(admin_mfa_identity, "TestCommand")

        non_admin_identity = OperatorIdentity(
            operator_id="viewer1",
            roles=["viewer"],
            mfa_verified=True,
        )
        with pytest.raises(HTTPException) as exc_info:
            require_admin_mfa(non_admin_identity, "TestCommand")
        assert exc_info.value.status_code == 403
        assert "requires 'admin' role" in str(exc_info.value.detail)

        admin_no_mfa_identity = OperatorIdentity(
            operator_id="admin2",
            roles=["admin"],
            mfa_verified=False,
        )
        with pytest.raises(HTTPException) as exc_info:
            require_admin_mfa(admin_no_mfa_identity, "TestCommand")
        assert exc_info.value.status_code == 403
        assert "requires MFA" in str(exc_info.value.detail)


class TestJWKSPrewarmAndFailClosed:
    """Verifies JWKS prewarm across direct URI, discovery, failure swallowing, and fail-closed auth."""

    def test_prewarm_noop_when_no_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("PANTHEON_BFF_JWKS_URI", raising=False)
        monkeypatch.delenv("PANTHEON_RUNTIME_JWKS_URI", raising=False)
        monkeypatch.delenv("PANTHEON_BFF_OIDC_DISCOVERY_URL", raising=False)
        monkeypatch.delenv("PANTHEON_RUNTIME_OIDC_DISCOVERY_URL", raising=False)

        with patch("services.runtime_auth_inbound._fetch_jwks_keys") as fetch_keys, patch(
            "services.runtime_auth_inbound._fetch_oidc_metadata"
        ) as fetch_meta:
            _prewarm_jwks_cache()

        fetch_keys.assert_not_called()
        fetch_meta.assert_not_called()

    def test_prewarm_direct_uri(self, monkeypatch: pytest.MonkeyPatch) -> None:
        uri = "https://auth.example.com/oauth/jwks.json"
        monkeypatch.setenv("PANTHEON_BFF_JWKS_URI", uri)
        monkeypatch.delenv("PANTHEON_BFF_OIDC_DISCOVERY_URL", raising=False)

        with patch("services.runtime_auth_inbound._fetch_jwks_keys") as fetch_keys:
            _prewarm_jwks_cache()

        fetch_keys.assert_called_once_with(uri)

    def test_prewarm_discovery_url_resolution(self, monkeypatch: pytest.MonkeyPatch) -> None:
        disc_url = "https://auth.example.com/.well-known/openid-configuration"
        resolved_jwks = "https://auth.example.com/keys"
        monkeypatch.delenv("PANTHEON_BFF_JWKS_URI", raising=False)
        monkeypatch.setenv("PANTHEON_BFF_OIDC_DISCOVERY_URL", disc_url)

        with patch(
            "services.runtime_auth_inbound._fetch_oidc_metadata",
            return_value={"jwks_uri": resolved_jwks},
        ) as fetch_meta, patch("services.runtime_auth_inbound._fetch_jwks_keys") as fetch_keys:
            _prewarm_jwks_cache()

        fetch_meta.assert_called_once_with(disc_url)
        fetch_keys.assert_called_once_with(resolved_jwks)

    def test_prewarm_bounded_failure_swallowing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PANTHEON_BFF_JWKS_URI", "https://auth.example.com/jwks.json")
        with patch(
            "services.runtime_auth_inbound._fetch_jwks_keys",
            side_effect=TimeoutError("IDP network timed out"),
        ):
            # Must not raise or abort startup
            _prewarm_jwks_cache()

    def test_prewarm_populates_cache_and_serves_request_time(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import services.runtime_auth_inbound as auth_inbound

        uri = "https://auth.example.com/jwks-cache-test.json"
        monkeypatch.setenv("PANTHEON_BFF_JWKS_URI", uri)
        auth_inbound._JWKS_CACHE.pop(uri, None)

        fake_keys = [{"kid": "test-kid-1", "kty": "RSA"}]
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value.__enter__.return_value.read.return_value = (
                json.dumps({"keys": fake_keys}).encode()
            )
            _prewarm_jwks_cache()
            assert mock_urlopen.call_count == 1

            # Request time fetch hits cache: no second urlopen call
            keys = auth_inbound._fetch_jwks_keys(uri)
            assert keys == fake_keys
            assert mock_urlopen.call_count == 1

        auth_inbound._JWKS_CACHE.pop(uri, None)

    def test_auth_fails_closed_when_key_unavailable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When JWKS cannot resolve token key, auth fails closed with 401/403."""
        import urllib.error
        import services.runtime_auth_inbound as auth_inbound

        uri = "https://auth.example.com/jwks-failclosed.json"
        monkeypatch.setenv("PANTHEON_RUNTIME_JWKS_URI", uri)
        monkeypatch.setenv("PANTHEON_RUNTIME_AUTH_MODE", "strict")
        monkeypatch.setenv("PANTHEON_ENV", "production")
        auth_inbound._JWKS_CACHE.pop(uri, None)

        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("IDP down")):
            with pytest.raises(auth_inbound.AuthError) as exc_info:
                auth_inbound.validate_request_auth(
                    authorization="Bearer eyJhbGciOiJSUzI1NiIsImtpZCI6ImtpZDEifQ.eyJzdWIiOiJ1c2VyMSIsInJvbGVzIjpbIm9wZXJhdG9yIl19.c2ln",
                    required_roles=["operator"],
                    env={"PANTHEON_RUNTIME_AUTH_MODE": "strict", "PANTHEON_RUNTIME_JWKS_URI": uri},
                )
            assert exc_info.value.status_code in {401, 403}

        auth_inbound._JWKS_CACHE.pop(uri, None)


class TestLifespanAndProviderReadiness:
    """Verifies single ProviderReadinessCache, single refresh loop, and clean cancellation."""

    def test_lifespan_lifecycle_and_clean_shutdown(self) -> None:
        async def _run() -> None:
            probe_fn = AsyncMock(return_value={"provider": "mock", "healthy": True})
            cache = ProviderReadinessCache(probe=probe_fn, stale_after_seconds=10.0)

            tasks_created = []

            def tracking_task_factory(coro, *, name=None):
                task = asyncio.create_task(coro, name=name)
                tasks_created.append(task)
                return task

            lifespan_fn = create_lifespan(
                cache,
                interval_seconds=0.05,
                task_factory=tracking_task_factory,
                prewarm_jwks=False,
            )

            app = FastAPI()
            async with lifespan_fn(app):
                assert app.state.provider_readiness_cache is cache
                assert len(tasks_created) == 1
                refresh_task = app.state.provider_readiness_refresh_task
                assert not refresh_task.done()
                # Allow at least one refresh iteration
                await asyncio.sleep(0.08)
                assert probe_fn.call_count >= 1

            # After lifespan exits, task must be cancelled cleanly
            assert refresh_task.done()
            assert refresh_task.cancelled()

        asyncio.run(_run())

    def test_default_openclaw_provider_probe_contract(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Verify _default_openclaw_provider_probe against real OpenClawOpsClient contracts."""
        from services.control_plane.bff.openclaw_ops_client import OpenClawOpsClient, OpenClawOpsClientError

        # 1. Unconfigured: client.configured is False (no URL env vars)
        monkeypatch.delenv("PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL", raising=False)
        monkeypatch.delenv("PANTHEON_OPENCLAW_ADAPTER_URL", raising=False)
        monkeypatch.delenv("OPENCLAW_GATEWAY_ADAPTER_URL", raising=False)

        res_unconfigured = bff_main._default_openclaw_provider_probe()
        assert res_unconfigured["ready"] is False
        assert res_unconfigured["status"] == "unavailable"
        assert res_unconfigured["reason"] == "openclaw_adapter_unconfigured"

        # 2. Configured and Reachable: adapter returns reachable=True
        monkeypatch.setenv("PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL", "http://127.0.0.1:8104")
        with patch.object(
            OpenClawOpsClient,
            "get_upstream_status",
            return_value={"upstream_url": "http://upstream:8104", "reachable": True, "details": {"reachable": True}},
        ):
            res_healthy = bff_main._default_openclaw_provider_probe()
            assert res_healthy["ready"] is True
            assert res_healthy["status"] == "ready"
            assert res_healthy["raw"]["reachable"] is True

        # 3. Configured and Unreachable: adapter returns reachable=False
        with patch.object(
            OpenClawOpsClient,
            "get_upstream_status",
            return_value={"upstream_url": "http://upstream:8104", "reachable": False, "details": {"reachable": False}},
        ):
            res_unreachable = bff_main._default_openclaw_provider_probe()
            assert res_unreachable["ready"] is False
            assert res_unreachable["status"] == "unavailable"
            assert res_unreachable["raw"]["reachable"] is False

        # 4. Configured and Client Error (e.g. adapter 503 / connection failure)
        with patch.object(
            OpenClawOpsClient,
            "get_upstream_status",
            side_effect=OpenClawOpsClientError("Connection refused", status_code=503, error_code="UPSTREAM_UNAVAILABLE"),
        ):
            res_error = bff_main._default_openclaw_provider_probe()
            assert res_error["ready"] is False
            assert res_error["status"] == "unavailable"
            assert res_error["reason"] == "OpenClawOpsClientError"

    def test_production_lifespan_cache_readiness_healthy_and_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Lifespan, cache refresh, and auth_facade.readiness() correctly compose for both healthy and unavailable states."""
        from services.control_plane.bff.openclaw_ops_client import OpenClawOpsClient

        monkeypatch.setenv("PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL", "http://127.0.0.1:8104")

        async def _run() -> None:
            # Case 1: Healthy upstream
            with patch.object(
                OpenClawOpsClient,
                "get_upstream_status",
                return_value={"upstream_url": "http://upstream:8104", "reachable": True, "details": {"reachable": True}},
            ):
                cache = ProviderReadinessCache(
                    probe=bff_main._default_openclaw_provider_probe,
                    provider="openclaw",
                )
                lifespan_fn = create_lifespan(cache, interval_seconds=0.05, prewarm_jwks=False)
                auth_service = AuthFacadeService(
                    provider_readiness_cache=cache,
                    local_readiness=lambda **kw: {"data": {"ready": True, "authReady": True}},
                )

                app = FastAPI()
                async with lifespan_fn(app):
                    await asyncio.sleep(0.08)
                    readiness = await auth_service.readiness()
                    assert readiness["data"]["providerReady"] is True
                    assert readiness["data"]["provider"]["ready"] is True
                    assert readiness["data"]["provider"]["status"] == "ready"

            # Case 2: Unavailable upstream
            with patch.object(
                OpenClawOpsClient,
                "get_upstream_status",
                return_value={"upstream_url": "http://upstream:8104", "reachable": False, "details": {"reachable": False}},
            ):
                cache_unavail = ProviderReadinessCache(
                    probe=bff_main._default_openclaw_provider_probe,
                    provider="openclaw",
                )
                lifespan_fn_unavail = create_lifespan(cache_unavail, interval_seconds=0.05, prewarm_jwks=False)
                auth_service_unavail = AuthFacadeService(
                    provider_readiness_cache=cache_unavail,
                    local_readiness=lambda **kw: {"data": {"ready": True, "authReady": True}},
                )

                app_unavail = FastAPI()
                async with lifespan_fn_unavail(app_unavail):
                    await asyncio.sleep(0.08)
                    readiness_unavail = await auth_service_unavail.readiness()
                    assert readiness_unavail["data"]["providerReady"] is False
                    assert readiness_unavail["data"]["provider"]["ready"] is False
                    assert readiness_unavail["data"]["provider"]["status"] == "unavailable"

        asyncio.run(_run())


class TestCORSAndSecurityHeaders:
    """Verifies CORS preflight 204, origin filtering, and security headers including SSE streaming."""

    def test_cors_preflight_204(self, monkeypatch: pytest.MonkeyPatch) -> None:
        allowed_origin = "https://app.dev.mvl-cap.tw"
        monkeypatch.setenv("PANTHEON_BFF_CORS_ORIGINS", allowed_origin)

        app = build_bff_app(title="Test BFF")

        @app.post("/test-resource")
        def create_resource():
            return {"status": "ok"}

        with TestClient(app) as client:
            resp = client.options(
                "/test-resource",
                headers={
                    "Origin": allowed_origin,
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": "Content-Type,Authorization",
                },
            )
            assert resp.status_code == 204
            assert resp.headers["Access-Control-Allow-Origin"] == allowed_origin
            assert resp.headers["Access-Control-Allow-Credentials"] == "true"
            assert "POST" in resp.headers["Access-Control-Allow-Methods"]

    def test_cors_disallowed_origin(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PANTHEON_BFF_CORS_ORIGINS", "https://app.dev.mvl-cap.tw")
        app = build_bff_app(title="Test BFF")

        with TestClient(app) as client:
            resp = client.options(
                "/test-resource",
                headers={
                    "Origin": "https://malicious.attacker.com",
                    "Access-Control-Request-Method": "POST",
                },
            )
            assert resp.status_code == 400 or "Access-Control-Allow-Origin" not in resp.headers

    def test_security_headers_on_standard_and_streaming_responses(self) -> None:
        app = build_bff_app(title="Test BFF")

        @app.get("/normal")
        def normal_endpoint():
            return {"data": "ok"}

        @app.get("/stream")
        def stream_endpoint():
            async def event_generator():
                yield "event: update\ndata: {}\n\n"
            return StreamingResponse(event_generator(), media_type="text/event-stream")

        with TestClient(app) as client:
            resp_normal = client.get("/normal")
            assert resp_normal.headers["x-content-type-options"] == "nosniff"
            assert resp_normal.headers["x-frame-options"] == "DENY"
            assert "referrer-policy" in resp_normal.headers

            resp_stream = client.get("/stream")
            assert resp_stream.headers["x-content-type-options"] == "nosniff"
            assert resp_stream.headers["x-frame-options"] == "DENY"
            assert "referrer-policy" in resp_stream.headers


class TestPackDErrorEnvelopesAndCORS:
    """Verifies Pack D error formatting and CORS preservation on 401, 403, 422, and 500."""

    @pytest.fixture
    def error_app(self, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
        allowed_origin = "https://app.dev.mvl-cap.tw"
        monkeypatch.setenv("PANTHEON_BFF_CORS_ORIGINS", allowed_origin)
        app = build_bff_app(title="Error Test BFF")

        class ItemPayload(BaseModel):
            item_id: str
            count: int

        @app.get("/test-401")
        def raise_401():
            raise HTTPException(status_code=401, detail="Authentication credentials required")

        @app.get("/test-403")
        def raise_403():
            raise HTTPException(status_code=403, detail="Operator permission required")

        @app.post("/test-422")
        def raise_422(payload: ItemPayload):
            return payload

        @app.get("/test-500")
        def raise_500():
            raise RuntimeError("Database connection reset by peer")

        return app

    def test_401_envelope_and_cors(self, error_app: FastAPI) -> None:
        origin = "https://app.dev.mvl-cap.tw"
        with TestClient(error_app) as client:
            resp = client.get(
                "/test-401",
                headers={"Origin": origin, "X-Correlation-Id": "corr-401-test"},
            )
            assert resp.status_code == 401
            body = resp.json()
            assert body["error"]["code"] == "AUTH_REQUIRED"
            assert body["meta"]["correlationId"] == "corr-401-test"
            assert resp.headers["Access-Control-Allow-Origin"] == origin
            assert resp.headers["Access-Control-Allow-Credentials"] == "true"

    def test_403_envelope_and_cors(self, error_app: FastAPI) -> None:
        origin = "https://app.dev.mvl-cap.tw"
        with TestClient(error_app) as client:
            resp = client.get(
                "/test-403",
                headers={"Origin": origin, "X-Correlation-Id": "corr-403-test"},
            )
            assert resp.status_code == 403
            body = resp.json()
            assert body["error"]["code"] == "FORBIDDEN"
            assert body["meta"]["correlationId"] == "corr-403-test"
            assert resp.headers["Access-Control-Allow-Origin"] == origin

    def test_422_envelope_and_cors(self, error_app: FastAPI) -> None:
        origin = "https://app.dev.mvl-cap.tw"
        with TestClient(error_app) as client:
            resp = client.post(
                "/test-422",
                json={"item_id": "item-1"},  # missing 'count'
                headers={"Origin": origin, "X-Correlation-Id": "corr-422-test"},
            )
            assert resp.status_code == 422
            body = resp.json()
            assert body["error"]["code"] == "VALIDATION_FAILED"
            assert body["meta"]["correlationId"] == "corr-422-test"
            assert resp.headers["Access-Control-Allow-Origin"] == origin

    def test_500_envelope_masks_internal_details_and_preserves_cors(self, error_app: FastAPI) -> None:
        origin = "https://app.dev.mvl-cap.tw"
        with TestClient(error_app, raise_server_exceptions=False) as client:
            resp = client.get(
                "/test-500",
                headers={"Origin": origin, "X-Correlation-Id": "corr-500-test"},
            )
            assert resp.status_code == 500
            body = resp.json()
            assert body["error"]["code"] == "INTERNAL_ERROR"
            assert "Database connection reset by peer" not in resp.text
            assert body["meta"]["correlationId"] == "corr-500-test"
            assert resp.headers["Access-Control-Allow-Origin"] == origin
            assert resp.headers["Access-Control-Allow-Credentials"] == "true"

    def test_error_envelope_preserves_canonical_correlation_when_source_contains_meta(self) -> None:
        """Source detail containing 'meta' or upstream 'correlationId' must not overwrite canonical request correlation."""
        app = build_bff_app(title="Correlation Preservation Test")

        @app.get("/test-upstream-corr")
        def upstream_corr_route():
            raise HTTPException(
                status_code=400,
                detail={
                    "error": {
                        "code": "DOWNSTREAM_ERROR",
                        "message": "Downstream rejected request",
                    },
                    "meta": {
                        "correlationId": "upstream-corr-9999",
                        "service": "downstream-svc",
                    },
                },
            )

        with TestClient(app) as client:
            resp = client.get(
                "/test-upstream-corr",
                headers={"X-Correlation-Id": "request-corr-1234"},
            )
            assert resp.status_code == 400
            assert resp.headers["X-Correlation-Id"] == "request-corr-1234"
            body = resp.json()
            assert body["meta"]["correlationId"] == "request-corr-1234"
            assert body["error"]["code"] == "UPSTREAM_ERROR"
            assert body["error"]["message"] == "Downstream rejected request"
