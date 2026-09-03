"""Independent verification: POST /api/telemetry/replay authorization boundary.

PSD-TELEMETRY-VERIFY-001 verifies the authorization boundary landed by
PSD-TELEMETRY-AUTH-001 independently of that task's own unit test
(tests/scripts/test_bootstrap_telemetry_replay.py), which only proves
scripts/bootstrap.sh forwards a caller-supplied header -- it stubs out
urllib.request.urlopen and never exercises the real telemetry service's
auth decision. This file drives the real Flask route
(@require_telemetry_authority(("operator", "admin"))) with real JWTs under
strict mode, so the four paths named in the task's acceptance criteria are
each proven against production auth code, not a test double:

  * authorized   -- a verified operator/admin-role JWT is admitted (200)
  * unauthorized -- a verified JWT with an out-of-scope role is refused (403)
  * expired      -- a verified but time-expired JWT is refused (401)
  * missing      -- no Authorization header at all is refused (401)

Run with:
    python3 -m unittest services.telemetry.test_replay_authorization_boundary
"""
from __future__ import annotations

import asyncio
import os
import sys
import threading
import time
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

import services.telemetry.main as _main
from services.runtime_auth_inbound import encode_jwt_hs256
from services.telemetry.dead_letter import TAG_WRITER_ERROR
from services.telemetry.ingest_svc import TelemetryIngestService
from services.telemetry.runtime_summary import RuntimeSummaryProjectionStore
from services.telemetry.trade_episode_projection import TradeEpisodeProjectionStore

_TENANT_ID = "tenant-alpha"
_JWT_SECRET = "psd-telemetry-verify-001-secret"
_BINDING_ID = "verify-binding-001"


class _StubBindingStore:
    """Minimal binding store satisfying the E-1 evidence contract."""

    def get_binding(self, binding_id: str):
        if binding_id != _BINDING_ID:
            return None
        return types.SimpleNamespace(
            binding_id=_BINDING_ID,
            runtime_id="verify-runtime-1",
            capital_pool_id="verify-pool-1",
            artifact_id="verify-artifact-1",
            artifact_version="1.0.0",
            plan_id="verify-plan-1",
            persona_capital_binding_id="verify-pcb-1",
            deployment_mode="paper",
            execution_mode="paper",
            effective_at="2026-01-01T00:00:00Z",
            retired_at=None,
        )

_STRICT_ENV = {
    "PANTHEON_TELEMETRY_AUTH_MODE": "strict",
    "PANTHEON_TELEMETRY_JWT_SECRET": _JWT_SECRET,
    "PANTHEON_TELEMETRY_ALLOWED_TENANTS": _TENANT_ID,
    # An unrelated runtime-manager default role must not leak into telemetry
    # authority; PSD-TELEMETRY-AUTH-001's own boundary depends on this.
    "PANTHEON_RUNTIME_DEFAULT_ROLE": "admin",
}


def _reject_one_dlq_entry(event_id: str) -> None:
    _main._svc._dlq.reject(
        {
            "tenant_id": _TENANT_ID,
            "event_id": event_id,
            "event_type": "pnl_snapshot",
            "created_at": "2026-01-01T00:00:00Z",
            "execution_mode": "paper",
            "environment": "paper",
            "deployment_stage": "paper",
            "binding_id": _BINDING_ID,
            "runtime_id": "verify-runtime-1",
            "capital_pool_id": "verify-pool-1",
            "artifact_id": "verify-artifact-1",
            "artifact_version": "1.0.0",
            "plan_id": "verify-plan-1",
            "persona_capital_binding_id": "verify-pcb-1",
            "target": {"strategy_id": "verify-strategy"},
            "metrics": {"pnl": 1.0},
            "metadata": {},
            "correlation_envelope": {"tenant_id": _TENANT_ID},
        },
        tags=[TAG_WRITER_ERROR],
        reason="PSD-TELEMETRY-VERIFY-001 synthetic write-failure entry",
    )


def _operator_token(**overrides) -> str:
    claims = {
        "sub": "verify-operator",
        "roles": ["operator"],
        "exp": time.time() + 3600,
    }
    claims.update(overrides)
    return encode_jwt_hs256(claims, secret=_JWT_SECRET)


class TestReplayAuthorizationBoundary(unittest.TestCase):
    """Independent proof of the replay route's operator/admin authority gate."""

    @classmethod
    def setUpClass(cls):
        cls._old_env = {key: os.environ.get(key) for key in _STRICT_ENV}
        cls._old_runtime_manager_url = os.environ.get("PANTHEON_RUNTIME_MANAGER_URL")
        os.environ.update(_STRICT_ENV)
        os.environ["PANTHEON_RUNTIME_MANAGER_URL"] = "http://runtime-manager.test"

        loop = asyncio.new_event_loop()

        def _run_loop():
            asyncio.set_event_loop(loop)
            loop.run_forever()

        thread = threading.Thread(
            target=_run_loop, daemon=True, name="test-replay-auth-loop"
        )
        thread.start()
        cls._loop = loop

        svc = TelemetryIngestService(
            batch_size=10,
            batch_interval=0.05,
            binding_store=_StubBindingStore(),
            runtime_summary_store=RuntimeSummaryProjectionStore(
                heartbeat_stale_after_seconds=10_000_000_000
            ),
            trade_episode_projection_store=TradeEpisodeProjectionStore(),
        )
        asyncio.run_coroutine_threadsafe(svc.start(), loop).result(timeout=5)

        _main._loop = loop
        _main._svc = svc
        cls._svc = svc
        cls.client = _main.app.test_client()

    @classmethod
    def tearDownClass(cls):
        asyncio.run_coroutine_threadsafe(
            cls._svc.stop(graceful=True), cls._loop
        ).result(timeout=5)
        _main._svc = None
        if cls._loop and cls._loop.is_running():
            cls._loop.call_soon_threadsafe(cls._loop.stop)
        _main._loop = None
        for key, value in cls._old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        if cls._old_runtime_manager_url is None:
            os.environ.pop("PANTHEON_RUNTIME_MANAGER_URL", None)
        else:
            os.environ["PANTHEON_RUNTIME_MANAGER_URL"] = cls._old_runtime_manager_url

    def _post_replay(self, headers: dict[str, str]):
        return self.client.post("/api/telemetry/replay", headers=headers)

    # --- authorized ---

    def test_authorized_operator_token_replays(self):
        _reject_one_dlq_entry("verify-authorized-001")
        token = _operator_token()

        resp = self._post_replay(
            {"Authorization": f"Bearer {token}", "X-Tenant-Id": _TENANT_ID}
        )

        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        self.assertGreaterEqual(resp.get_json()["replayed"], 1)

    def test_authorized_admin_token_replays(self):
        _reject_one_dlq_entry("verify-authorized-admin-001")
        token = _operator_token(sub="verify-admin", roles=["admin"])

        resp = self._post_replay(
            {"Authorization": f"Bearer {token}", "X-Tenant-Id": _TENANT_ID}
        )

        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        self.assertGreaterEqual(resp.get_json()["replayed"], 1)

    # --- unauthorized (authenticated, wrong role) ---

    def test_unauthorized_role_is_refused(self):
        token = _operator_token(sub="verify-viewer", roles=["reviewer"])

        resp = self._post_replay(
            {"Authorization": f"Bearer {token}", "X-Tenant-Id": _TENANT_ID}
        )

        self.assertEqual(resp.status_code, 403, resp.get_data(as_text=True))
        self.assertEqual(resp.get_json()["error"]["code"], "AUTH_FORBIDDEN")

    def test_service_token_without_operator_role_is_refused(self):
        # PSD-GAP-04's closure semantics: the bootstrap service token must
        # never be treated as sufficient authority for replay.
        with patch.dict(
            os.environ,
            {
                "PANTHEON_TELEMETRY_SERVICE_TOKEN": "verify-service-secret",
                "PANTHEON_TELEMETRY_SERVICE_TENANTS": _TENANT_ID,
            },
        ):
            resp = self._post_replay(
                {
                    "Authorization": "Bearer verify-service-secret",
                    "X-Tenant-Id": _TENANT_ID,
                }
            )

        self.assertEqual(resp.status_code, 403, resp.get_data(as_text=True))
        self.assertEqual(resp.get_json()["error"]["code"], "AUTH_FORBIDDEN")

    # --- expired ---

    def test_expired_token_is_refused(self):
        token = _operator_token(exp=time.time() - 60)

        resp = self._post_replay(
            {"Authorization": f"Bearer {token}", "X-Tenant-Id": _TENANT_ID}
        )

        self.assertEqual(resp.status_code, 401, resp.get_data(as_text=True))
        self.assertEqual(resp.get_json()["error"]["code"], "AUTH_JWT_EXPIRED")

    # --- missing authorization ---

    def test_missing_authorization_header_is_refused(self):
        resp = self._post_replay({"X-Tenant-Id": _TENANT_ID})

        self.assertEqual(resp.status_code, 401, resp.get_data(as_text=True))

    def test_empty_bearer_token_is_refused(self):
        resp = self._post_replay(
            {"Authorization": "Bearer ", "X-Tenant-Id": _TENANT_ID}
        )

        self.assertEqual(resp.status_code, 401, resp.get_data(as_text=True))

    # --- no secrets in the response body on any path ---

    def test_no_evidence_response_leaks_the_presented_token(self):
        secret_token = _operator_token(sub="verify-secret-leak-check")
        rejected = self._post_replay(
            {"Authorization": f"Bearer {secret_token}"}  # missing X-Tenant-Id
        )
        expired_token = _operator_token(exp=time.time() - 60)
        expired = self._post_replay(
            {"Authorization": f"Bearer {expired_token}", "X-Tenant-Id": _TENANT_ID}
        )

        self.assertNotIn(secret_token, rejected.get_data(as_text=True))
        self.assertNotIn(expired_token, expired.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
