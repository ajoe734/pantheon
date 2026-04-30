"""Tests for the Pantheon-owned OpenClaw session lifecycle.

Covers SVC-OPENCLAW-SESSION-LIFECYCLE acceptance:
- session create/get/list/cancel have durable state and idempotency
- operator ownership and audit metadata are recorded
- degraded upstream recovery preserves known session state
- broker/paper/live execution remains disabled by default
- tests cover duplicate create, cancel, and upstream failure
"""
from __future__ import annotations

import os
import sys
import types
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Bootstrap (re-uses the same stub pattern as test_main.py)
# ---------------------------------------------------------------------------

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _stub_foundation() -> None:
    if "services.foundation.health" in sys.modules:
        return
    try:
        import services.foundation.health  # noqa: F401
        return
    except ImportError:
        pass
    for pkg in ("services", "services.foundation"):
        if pkg not in sys.modules:
            sys.modules[pkg] = types.ModuleType(pkg)
    health_mod = types.ModuleType("services.foundation.health")

    def health_payload(service, *, live=True, ready=True, dependencies=None, metrics=None, details=None):
        dep = {}
        if dependencies:
            resolved = dependencies() if callable(dependencies) else dependencies
            for key, value in resolved.items():
                dep[key] = value() if callable(value) else value
        statuses = [str(v.get("status", "ok")).lower() for v in dep.values() if isinstance(v, dict)]
        status = "ok" if live and ready else "error"
        if status == "ok" and any(s in {"degraded", "error", "unavailable", "failed"} for s in statuses):
            status = "degraded"
        return {"status": status, "service": service, "live": live, "ready": ready, "dependencies": dep}

    def readiness_status_code(payload):
        return 200 if payload.get("status") == "ok" else 503

    def register_fastapi_health_routes(app, service, *, dependencies=None, metrics=None, details=None):
        from fastapi.responses import JSONResponse

        async def healthz():
            return health_payload(service, dependencies=dependencies, details=details)

        async def livez():
            return health_payload(service, ready=True)

        async def readyz():
            payload = health_payload(service, dependencies=dependencies, details=details)
            return JSONResponse(payload, status_code=readiness_status_code(payload))

        async def metrics_route():
            return {"service": service}

        app.add_api_route("/healthz", healthz, methods=["GET"])
        app.add_api_route("/livez", livez, methods=["GET"])
        app.add_api_route("/readyz", readyz, methods=["GET"])
        app.add_api_route("/metrics", metrics_route, methods=["GET"])

    health_mod.health_payload = health_payload
    health_mod.readiness_status_code = readiness_status_code
    health_mod.register_fastapi_health_routes = register_fastapi_health_routes
    sys.modules["services.foundation.health"] = health_mod
    sys.modules["services.foundation"] = sys.modules.get("services.foundation") or types.ModuleType(
        "services.foundation"
    )


_stub_foundation()

_ADAPTER_DIR = os.path.dirname(__file__)
if _ADAPTER_DIR not in sys.path:
    sys.path.insert(0, _ADAPTER_DIR)

import session_lifecycle as lifecycle  # noqa: E402


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _UpstreamError(Exception):
    """Mimics adapter_main.UpstreamClientError without importing main."""

    def __init__(self, *, error_code: str, retryable: bool, status_code: int = 503, message: str = "stub"):
        super().__init__(message)
        self.error_code = error_code
        self.retryable = retryable
        self.status_code = status_code
        self.message = message

    def to_payload(self) -> Dict[str, Any]:
        return {
            "status": "upstream_error",
            "error_code": self.error_code,
            "message": self.message,
            "retryable": self.retryable,
        }


class _FakeUpstream:
    """Fake upstream client for lifecycle tests."""

    def __init__(self) -> None:
        self.create_calls: List[Any] = []
        self.cancel_calls: List[str] = []
        self.get_calls: List[str] = []
        self.create_response: Optional[Dict[str, Any]] = None
        self.create_error: Optional[Exception] = None
        self.cancel_response: Optional[Dict[str, Any]] = None
        self.cancel_error: Optional[Exception] = None
        self.get_response: Optional[Dict[str, Any]] = None
        self.get_error: Optional[Exception] = None

    def create_session(self, req: Any) -> Dict[str, Any]:
        self.create_calls.append(req)
        if self.create_error:
            raise self.create_error
        return self.create_response or {"session_id": "upstream-1", "status": "created"}

    def cancel_session(self, session_id: str) -> Dict[str, Any]:
        self.cancel_calls.append(session_id)
        if self.cancel_error:
            raise self.cancel_error
        return self.cancel_response or {"session_id": session_id, "status": "cancel_requested"}

    def get_session(self, session_id: str) -> Dict[str, Any]:
        self.get_calls.append(session_id)
        if self.get_error:
            raise self.get_error
        return self.get_response or {"session_id": session_id, "status": "active"}

    def list_sessions(self) -> List[Dict[str, Any]]:
        return []


def _build_store(tmp: Path, upstream: Optional[_FakeUpstream] = None) -> lifecycle.SessionLifecycleStore:
    return lifecycle.SessionLifecycleStore(
        storage_path=tmp / "lifecycle.json",
        upstream_factory=lambda: upstream,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCreateSession(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def test_create_succeeds_when_upstream_available(self) -> None:
        upstream = _FakeUpstream()
        upstream.create_response = {"session_id": "up-123", "status": "running"}
        store = _build_store(self.tmp, upstream)
        record, replayed = store.create_session(
            agent_id="agent-x",
            session_type="interactive",
            operator_id="alice",
            idempotency_key="key-1",
        )
        self.assertFalse(replayed)
        self.assertEqual(record.state, "active")
        self.assertEqual(record.upstream_session_id, "up-123")
        self.assertEqual(record.operator_id, "alice")
        self.assertEqual(record.idempotency_key, "key-1")
        # audit log should record both the local create_requested and the upstream ack.
        actions = [event["action"] for event in record.audit_log]
        self.assertIn("create_requested", actions)
        self.assertIn("create_acknowledged", actions)
        # upstream call shape preserved.
        self.assertEqual(upstream.create_calls[0].agent_id, "agent-x")
        self.assertEqual(upstream.create_calls[0].session_type, "interactive")

    def test_create_idempotent_with_same_key_does_not_call_upstream_twice(self) -> None:
        upstream = _FakeUpstream()
        store = _build_store(self.tmp, upstream)
        first, replayed_first = store.create_session(
            agent_id="agent-x",
            session_type="interactive",
            operator_id="alice",
            idempotency_key="key-dup",
        )
        second, replayed_second = store.create_session(
            agent_id="agent-x",
            session_type="interactive",
            operator_id="alice",
            idempotency_key="key-dup",
        )
        self.assertFalse(replayed_first)
        self.assertTrue(replayed_second)
        self.assertEqual(first.session_id, second.session_id)
        self.assertEqual(len(upstream.create_calls), 1)

    def test_create_idempotency_key_with_different_payload_is_rejected(self) -> None:
        upstream = _FakeUpstream()
        store = _build_store(self.tmp, upstream)
        store.create_session(
            agent_id="agent-x",
            session_type="interactive",
            operator_id="alice",
            idempotency_key="key-conflict",
        )
        with self.assertRaises(lifecycle.LifecycleError) as ctx:
            store.create_session(
                agent_id="agent-y",
                session_type="interactive",
                operator_id="alice",
                idempotency_key="key-conflict",
            )
        self.assertEqual(ctx.exception.error_code, "LIFECYCLE_IDEMPOTENCY_CONFLICT")
        self.assertEqual(ctx.exception.status_code, 409)

    def test_create_records_state_lost_when_upstream_retryable_error(self) -> None:
        upstream = _FakeUpstream()
        upstream.create_error = _UpstreamError(error_code="UPSTREAM_TIMEOUT", retryable=True, status_code=504)
        store = _build_store(self.tmp, upstream)
        record, _ = store.create_session(
            agent_id="agent-x",
            session_type="interactive",
            operator_id="alice",
            idempotency_key=None,
        )
        self.assertEqual(record.state, "lost")
        self.assertIsNotNone(record.last_error)
        self.assertEqual(record.last_error["error_code"], "UPSTREAM_TIMEOUT")
        # later get_session call without upstream should still return the local record
        # without losing audit data.
        readback = store.get_session(record.session_id)
        self.assertEqual(readback.state, "lost")
        self.assertEqual(readback.session_id, record.session_id)

    def test_create_records_state_failed_when_upstream_non_retryable_error(self) -> None:
        upstream = _FakeUpstream()
        upstream.create_error = _UpstreamError(
            error_code="UPSTREAM_AUTH_DENIED", retryable=False, status_code=403
        )
        store = _build_store(self.tmp, upstream)
        record, _ = store.create_session(
            agent_id="agent-x",
            session_type="interactive",
            operator_id="alice",
            idempotency_key=None,
        )
        self.assertEqual(record.state, "failed")
        self.assertEqual(record.last_error["error_code"], "UPSTREAM_AUTH_DENIED")

    def test_create_without_upstream_marks_session_lost_for_recovery(self) -> None:
        store = lifecycle.SessionLifecycleStore(
            storage_path=self.tmp / "lifecycle.json",
            upstream_factory=lambda: None,
        )
        record, _ = store.create_session(
            agent_id="agent-x",
            session_type="interactive",
            operator_id="alice",
            idempotency_key="key-degraded",
        )
        self.assertEqual(record.state, "lost")
        # the durable record still exists for degraded recovery
        readback = store.get_session(record.session_id)
        self.assertEqual(readback.state, "lost")
        # listing also returns it
        listed = store.list_sessions()
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0].session_id, record.session_id)

    def test_create_requires_operator_id(self) -> None:
        store = _build_store(self.tmp, _FakeUpstream())
        with self.assertRaises(lifecycle.LifecycleError) as ctx:
            store.create_session(
                agent_id="agent-x",
                session_type="interactive",
                operator_id="",
                idempotency_key=None,
            )
        self.assertEqual(ctx.exception.status_code, 401)
        self.assertEqual(ctx.exception.error_code, "LIFECYCLE_OPERATOR_REQUIRED")


class TestCancelSession(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def test_cancel_active_session_succeeds(self) -> None:
        upstream = _FakeUpstream()
        store = _build_store(self.tmp, upstream)
        record, _ = store.create_session(
            agent_id="agent-x",
            session_type="interactive",
            operator_id="alice",
            idempotency_key=None,
        )
        canceled = store.cancel_session(record.session_id, operator_id="alice")
        self.assertEqual(canceled.state, "canceled")
        self.assertEqual(canceled.canceled_by, "alice")
        actions = [event["action"] for event in canceled.audit_log]
        self.assertIn("cancel_requested", actions)
        self.assertIn("cancel_acknowledged", actions)
        # cancel routed to upstream by upstream_session_id
        self.assertEqual(upstream.cancel_calls, ["upstream-1"])

    def test_cancel_replayed_when_already_canceled(self) -> None:
        store = _build_store(self.tmp, _FakeUpstream())
        record, _ = store.create_session(
            agent_id="agent-x",
            session_type="interactive",
            operator_id="alice",
            idempotency_key=None,
        )
        first = store.cancel_session(record.session_id, operator_id="alice")
        second = store.cancel_session(record.session_id, operator_id="alice")
        self.assertEqual(first.state, "canceled")
        self.assertEqual(second.state, "canceled")
        # second cancel did not append a new state-transition event
        cancel_count = sum(1 for e in second.audit_log if e["action"] == "cancel_acknowledged")
        self.assertEqual(cancel_count, 1)

    def test_cancel_records_lost_when_upstream_unreachable(self) -> None:
        upstream = _FakeUpstream()
        store = _build_store(self.tmp, upstream)
        record, _ = store.create_session(
            agent_id="agent-x",
            session_type="interactive",
            operator_id="alice",
            idempotency_key=None,
        )
        upstream.cancel_error = _UpstreamError(
            error_code="UPSTREAM_UNAVAILABLE", retryable=True, status_code=503
        )
        result = store.cancel_session(record.session_id, operator_id="alice")
        # state should be lost (recoverable later) — the cancel intent is preserved
        # because cancel_requested transitioned successfully and the upstream failure
        # was recorded as last_error.
        self.assertEqual(result.state, "lost")
        self.assertEqual(result.canceled_by, "alice")
        self.assertEqual(result.last_error["error_code"], "UPSTREAM_UNAVAILABLE")

    def test_cancel_marks_failed_on_non_retryable_upstream_error(self) -> None:
        upstream = _FakeUpstream()
        store = _build_store(self.tmp, upstream)
        record, _ = store.create_session(
            agent_id="agent-x",
            session_type="interactive",
            operator_id="alice",
            idempotency_key=None,
        )
        upstream.cancel_error = _UpstreamError(
            error_code="UPSTREAM_NOT_FOUND", retryable=False, status_code=404
        )
        result = store.cancel_session(record.session_id, operator_id="alice")
        self.assertEqual(result.state, "failed")
        self.assertEqual(result.last_error["error_code"], "UPSTREAM_NOT_FOUND")

    def test_cancel_unknown_session_returns_404(self) -> None:
        store = _build_store(self.tmp, _FakeUpstream())
        with self.assertRaises(lifecycle.LifecycleError) as ctx:
            store.cancel_session("missing", operator_id="alice")
        self.assertEqual(ctx.exception.status_code, 404)

    def test_cancel_locally_when_no_upstream_session_id(self) -> None:
        # Create with no upstream, get a record stuck in 'lost' with no upstream id.
        store = lifecycle.SessionLifecycleStore(
            storage_path=self.tmp / "lifecycle.json",
            upstream_factory=lambda: None,
        )
        record, _ = store.create_session(
            agent_id="agent-x",
            session_type="interactive",
            operator_id="alice",
            idempotency_key=None,
        )
        canceled = store.cancel_session(record.session_id, operator_id="alice")
        self.assertEqual(canceled.state, "canceled")
        actions = [event["action"] for event in canceled.audit_log]
        self.assertIn("cancel_completed_locally", actions)


class TestDegradedRecovery(unittest.TestCase):
    """Acceptance: degraded upstream recovery preserves known session state."""

    def setUp(self) -> None:
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def test_get_active_session_marks_lost_on_upstream_failure(self) -> None:
        upstream = _FakeUpstream()
        store = _build_store(self.tmp, upstream)
        record, _ = store.create_session(
            agent_id="agent-x",
            session_type="interactive",
            operator_id="alice",
            idempotency_key=None,
        )
        self.assertEqual(record.state, "active")
        upstream.get_error = _UpstreamError(
            error_code="UPSTREAM_TIMEOUT", retryable=True, status_code=504
        )
        readback = store.get_session(record.session_id)
        self.assertEqual(readback.state, "lost")
        self.assertEqual(readback.last_error["error_code"], "UPSTREAM_TIMEOUT")

    def test_list_sessions_returns_all_local_records_even_when_upstream_down(self) -> None:
        store = lifecycle.SessionLifecycleStore(
            storage_path=self.tmp / "lifecycle.json",
            upstream_factory=lambda: None,
        )
        for i in range(3):
            store.create_session(
                agent_id=f"agent-{i}",
                session_type="interactive",
                operator_id="alice",
                idempotency_key=f"key-{i}",
            )
        sessions = store.list_sessions()
        self.assertEqual(len(sessions), 3)
        # all should be in 'lost' state because upstream is absent
        for record in sessions:
            self.assertEqual(record.state, "lost")

    def test_list_filters_by_operator_and_state(self) -> None:
        store = lifecycle.SessionLifecycleStore(
            storage_path=self.tmp / "lifecycle.json",
            upstream_factory=lambda: None,
        )
        store.create_session(
            agent_id="a", session_type="interactive", operator_id="alice", idempotency_key="ka"
        )
        store.create_session(
            agent_id="b", session_type="interactive", operator_id="bob", idempotency_key="kb"
        )
        alice_sessions = store.list_sessions(operator_id="alice")
        self.assertEqual(len(alice_sessions), 1)
        self.assertEqual(alice_sessions[0].operator_id, "alice")
        lost_sessions = store.list_sessions(state="lost")
        self.assertEqual(len(lost_sessions), 2)

    def test_get_active_session_upstream_reports_canceled_transitions_to_canceled(self) -> None:
        """Upstream reporting canceled for an active session must transition to canceled."""
        upstream = _FakeUpstream()
        store = _build_store(self.tmp, upstream)
        record, _ = store.create_session(
            agent_id="agent-x",
            session_type="interactive",
            operator_id="alice",
            idempotency_key=None,
        )
        self.assertEqual(record.state, "active")
        upstream.get_response = {"session_id": "upstream-1", "status": "canceled"}
        readback = store.get_session(record.session_id)
        self.assertEqual(readback.state, "canceled")
        actions = [e["action"] for e in readback.audit_log]
        self.assertIn("upstream_reported_canceled", actions)

    def test_get_cancel_requested_upstream_still_active_preserves_local_state(self) -> None:
        """cancel_requested must not regress to active when upstream still reports active."""
        upstream = _FakeUpstream()
        store = _build_store(self.tmp, upstream)
        record, _ = store.create_session(
            agent_id="agent-x",
            session_type="interactive",
            operator_id="alice",
            idempotency_key=None,
        )
        # Force state to cancel_requested to simulate cancel in-flight.
        store._transition(
            record.session_id,
            "cancel_requested",
            actor="alice",
            action="cancel_requested",
            detail={},
        )
        upstream.get_response = {"session_id": "upstream-1", "status": "active"}
        readback = store.get_session(record.session_id)
        self.assertEqual(readback.state, "cancel_requested")

    def test_durable_state_persists_across_store_instances(self) -> None:
        upstream = _FakeUpstream()
        store_a = _build_store(self.tmp, upstream)
        record, _ = store_a.create_session(
            agent_id="agent-x",
            session_type="interactive",
            operator_id="alice",
            idempotency_key="durable-key",
        )
        # Reopen from disk
        store_b = _build_store(self.tmp, upstream)
        readback = store_b.get_session(record.session_id, refresh_from_upstream=False)
        self.assertEqual(readback.session_id, record.session_id)
        self.assertEqual(readback.operator_id, "alice")
        # idempotency key should also persist so repeat creates are still no-ops
        replayed_record, replayed = store_b.create_session(
            agent_id="agent-x",
            session_type="interactive",
            operator_id="alice",
            idempotency_key="durable-key",
        )
        self.assertTrue(replayed)
        self.assertEqual(replayed_record.session_id, record.session_id)


class TestRouteIntegration(unittest.TestCase):
    """End-to-end via FastAPI TestClient."""

    @classmethod
    def setUpClass(cls) -> None:
        import importlib
        import tempfile

        cls._tmp = tempfile.TemporaryDirectory()
        os.environ["OPENCLAW_LIFECYCLE_STORE_PATH"] = str(Path(cls._tmp.name) / "lifecycle.json")
        # ensure main is reloaded so the lifecycle store picks up the new path
        if "main" in sys.modules:
            importlib.reload(sys.modules["main"])
        else:
            import main  # noqa: F401
        cls.adapter_main = sys.modules["main"]
        from fastapi.testclient import TestClient

        cls.client = TestClient(cls.adapter_main.app)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()
        os.environ.pop("OPENCLAW_LIFECYCLE_STORE_PATH", None)

    def setUp(self) -> None:
        # Reset durable store between tests for isolation.
        store_path = Path(os.environ["OPENCLAW_LIFECYCLE_STORE_PATH"])
        if store_path.exists():
            store_path.unlink()
        # Plug in a fresh fake upstream for each test.
        self.fake = _FakeUpstream()
        self._patch = unittest.mock.patch.object(
            self.adapter_main,
            "_lifecycle_upstream_factory",
            return_value=self.fake,
        )
        # We cannot patch the factory after the store has been instantiated, so
        # rebuild the store in-place to use the new factory.
        self.adapter_main._LIFECYCLE_STORE = self.adapter_main.SessionLifecycleStore(
            storage_path=os.environ["OPENCLAW_LIFECYCLE_STORE_PATH"],
            upstream_factory=lambda: self.fake,
        )

    def test_create_requires_operator_header(self) -> None:
        resp = self.client.post(
            "/api/openclaw-adapter/lifecycle/sessions",
            json={"agent_id": "a", "session_type": "interactive"},
        )
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()["error_code"], "LIFECYCLE_OPERATOR_REQUIRED")

    def test_create_returns_201_and_lists_session(self) -> None:
        resp = self.client.post(
            "/api/openclaw-adapter/lifecycle/sessions",
            json={"agent_id": "agent-1", "session_type": "interactive"},
            headers={"X-Operator-Id": "alice", "X-Idempotency-Key": "route-1"},
        )
        self.assertEqual(resp.status_code, 201)
        body = resp.json()
        self.assertEqual(body["status"], "ok")
        self.assertFalse(body["replayed"])
        session_id = body["session"]["session_id"]
        # Idempotent replay returns 200 with the same session id
        repeat = self.client.post(
            "/api/openclaw-adapter/lifecycle/sessions",
            json={"agent_id": "agent-1", "session_type": "interactive"},
            headers={"X-Operator-Id": "alice", "X-Idempotency-Key": "route-1"},
        )
        self.assertEqual(repeat.status_code, 200)
        repeat_body = repeat.json()
        self.assertTrue(repeat_body["replayed"])
        self.assertEqual(repeat_body["session"]["session_id"], session_id)
        # List shows it
        listing = self.client.get("/api/openclaw-adapter/lifecycle/sessions")
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(len(listing.json()["sessions"]), 1)

    def test_get_returns_audit_trail(self) -> None:
        create_resp = self.client.post(
            "/api/openclaw-adapter/lifecycle/sessions",
            json={"agent_id": "agent-2", "session_type": "interactive"},
            headers={"X-Operator-Id": "bob"},
        )
        session_id = create_resp.json()["session"]["session_id"]
        audit_resp = self.client.get(
            f"/api/openclaw-adapter/lifecycle/sessions/{session_id}/audit"
        )
        self.assertEqual(audit_resp.status_code, 200)
        body = audit_resp.json()
        self.assertEqual(body["operator_id"], "bob")
        actions = [event["action"] for event in body["audit_log"]]
        self.assertIn("create_requested", actions)

    def test_cancel_idempotent(self) -> None:
        create_resp = self.client.post(
            "/api/openclaw-adapter/lifecycle/sessions",
            json={"agent_id": "agent-3", "session_type": "interactive"},
            headers={"X-Operator-Id": "alice"},
        )
        session_id = create_resp.json()["session"]["session_id"]
        first = self.client.post(
            f"/api/openclaw-adapter/lifecycle/sessions/{session_id}/cancel",
            headers={"X-Operator-Id": "alice"},
        )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["session"]["state"], "canceled")
        second = self.client.post(
            f"/api/openclaw-adapter/lifecycle/sessions/{session_id}/cancel",
            headers={"X-Operator-Id": "alice"},
        )
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()["session"]["state"], "canceled")

    def test_get_unknown_returns_404(self) -> None:
        resp = self.client.get("/api/openclaw-adapter/lifecycle/sessions/does-not-exist")
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["error_code"], "LIFECYCLE_NOT_FOUND")

    def test_capability_snapshot_advertises_lifecycle_owner(self) -> None:
        resp = self.client.get("/api/openclaw-adapter/capabilities")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["session_lifecycle"]["owner"], "pantheon_adapter")
        self.assertEqual(body["session_lifecycle_state"], "activation_ready")
        # broker/paper/live must remain deferred in the same payload
        self.assertEqual(body["broker_execution"], "deferred")
        self.assertEqual(body["paper_adapter"], "deferred")
        self.assertEqual(body["live_adapter"], "deferred")


# Late mock import only used inside TestRouteIntegration.setUp — placed here
# to avoid pulling unittest.mock during module import in environments that
# don't need it.
import unittest.mock  # noqa: E402  isort:skip


if __name__ == "__main__":
    unittest.main()
