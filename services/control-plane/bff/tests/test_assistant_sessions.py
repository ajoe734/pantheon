"""Tests for assistant session and transcript store (ASST-KERNEL-003).

Covers:
- session create (user and kernel modes)
- kernel policy: requires reason, TTL, capability
- session expire: expired sessions reject new messages
- session revoke: revoked sessions reject new messages
- transcript readback: turns are stored and retrievable

Tests are self-contained: routes are exercised through a minimal FastAPI app
that uses isolated in-memory store instances so no state bleeds between tests.
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from assistant.mode_policy import (
    DEFAULT_KERNEL_TTL_SECONDS,
    MAX_KERNEL_TTL_SECONDS,
    ModePolicyViolation,
    command_classes_for_mode,
    create_session,
    mode_allows_command_broker,
    validate_session_request,
)
from assistant.control_mode import PASSPHRASE_HASH_ENV, ControlModeStore, passphrase_hash
from assistant.models import AssistantMode
from assistant.routes import create_assistant_router
from assistant.transcript_store import (
    AssistantSession,
    InMemorySessionStore,
    InMemoryTranscriptStore,
    SessionNotFoundError,
    SessionRejectedError,
    SessionStatus,
    TurnRole,
    assert_session_accepts_messages,
    build_session,
    build_turn,
)


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


class _FakeIdentity:
    """Minimal actor identity used in tests."""

    def __init__(
        self,
        operator_id: str = "op_test",
        roles: Optional[list] = None,
        capabilities: Optional[list] = None,
        mfa_verified: bool = False,
    ) -> None:
        self.operator_id = operator_id
        self.roles = roles or ["read"]
        self.mfa_verified = mfa_verified
        self.claims = {"capabilities": capabilities or []}


def _make_client(
    *,
    capabilities: Optional[list] = None,
    roles: Optional[list] = None,
    mfa_verified: bool = False,
    control_mode_store: Optional[ControlModeStore] = None,
) -> TestClient:
    """Build a test client with isolated in-memory stores."""
    identity = _FakeIdentity(
        capabilities=capabilities or [],
        roles=roles,
        mfa_verified=mfa_verified,
    )

    def _extract_identity(_auth: Optional[str]) -> _FakeIdentity:
        return identity

    def _require_read_role(_id: Any) -> None:
        pass

    session_store = InMemorySessionStore()
    transcript_store = InMemoryTranscriptStore()

    router = create_assistant_router(
        build_context_pack=lambda sid, req, actor: (_ for _ in ()).throw(NotImplementedError),
        extract_identity=_extract_identity,
        require_read_role=_require_read_role,
        session_store=session_store,
        transcript_store=transcript_store,
        control_mode_store=control_mode_store,
    )

    app = FastAPI()
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=True)


def _make_kernel_client() -> TestClient:
    return _make_client(capabilities=["assistant.kernel"])


def _make_control_client(store: ControlModeStore) -> TestClient:
    return _make_client(
        capabilities=["assistant.kernel.debug"],
        roles=["operator"],
        mfa_verified=True,
        control_mode_store=store,
    )


# ---------------------------------------------------------------------------
# Unit: transcript_store
# ---------------------------------------------------------------------------


class TestInMemorySessionStore:
    def test_create_and_get(self):
        store = InMemorySessionStore()
        session = build_session(
            mode=AssistantMode.USER,
            actor_id="op1",
            roles=["read"],
            capabilities=[],
        )
        store.create(session)
        fetched = store.get(session.session_id)
        assert fetched.session_id == session.session_id
        assert fetched.status == SessionStatus.ACTIVE

    def test_get_missing_raises(self):
        store = InMemorySessionStore()
        with pytest.raises(SessionNotFoundError):
            store.get("nonexistent_session_id")

    def test_revoke(self):
        store = InMemorySessionStore()
        session = build_session(
            mode=AssistantMode.USER,
            actor_id="op1",
            roles=[],
            capabilities=[],
        )
        store.create(session)
        revoked = store.revoke(session.session_id, reason="test revoke")
        assert revoked.status == SessionStatus.REVOKED
        assert revoked.revoke_reason == "test revoke"
        assert revoked.revoked_at is not None

    def test_revoke_idempotent(self):
        store = InMemorySessionStore()
        session = build_session(
            mode=AssistantMode.USER, actor_id="op1", roles=[], capabilities=[]
        )
        store.create(session)
        store.revoke(session.session_id)
        again = store.revoke(session.session_id)
        assert again.status == SessionStatus.REVOKED

    def test_expired_session_is_marked_lazily(self):
        store = InMemorySessionStore()
        session = build_session(
            mode=AssistantMode.USER,
            actor_id="op1",
            roles=[],
            capabilities=[],
            ttl_seconds=1,
        )
        store.create(session)
        assert session.status == SessionStatus.ACTIVE
        # Manually backdate expires_at to simulate expiry
        from dataclasses import replace as dc_replace
        expired_session = dc_replace(
            session,
            expires_at=(datetime.now(timezone.utc) - timedelta(seconds=1))
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z"),
        )
        store._sessions[session.session_id] = expired_session
        fetched = store.get(session.session_id)
        assert fetched.status == SessionStatus.EXPIRED

    def test_update_context(self):
        store = InMemorySessionStore()
        session = build_session(
            mode=AssistantMode.USER, actor_id="op1", roles=[], capabilities=[]
        )
        store.create(session)
        updated = store.update_context(
            session.session_id,
            context_pack_id="ctx_abc",
            provider_run_id="run_xyz",
            audit_ref="audit_001",
        )
        assert updated.context_pack_id == "ctx_abc"
        assert updated.provider_run_id == "run_xyz"
        assert "audit_001" in updated.audit_refs


class TestInMemoryTranscriptStore:
    def test_append_and_list(self):
        store = InMemoryTranscriptStore()
        t1 = build_turn(session_id="sess1", role=TurnRole.USER, content="hello")
        t2 = build_turn(session_id="sess1", role=TurnRole.ASSISTANT, content="world")
        store.append(t1)
        store.append(t2)
        turns = store.list_turns("sess1")
        assert len(turns) == 2
        assert turns[0].role == TurnRole.USER
        assert turns[1].role == TurnRole.ASSISTANT

    def test_list_empty_session(self):
        store = InMemoryTranscriptStore()
        turns = store.list_turns("no_such_session")
        assert turns == []

    def test_turn_stores_source_refs(self):
        store = InMemoryTranscriptStore()
        refs = [{"source_id": "control_room", "href": "/bff/v5/control-room"}]
        turn = build_turn(
            session_id="sess2",
            role=TurnRole.USER,
            content="test",
            source_refs=refs,
        )
        store.append(turn)
        stored = store.list_turns("sess2")[0]
        assert stored.source_refs == refs


class TestAssertSessionAcceptsMessages:
    def test_active_session_passes(self):
        session = build_session(
            mode=AssistantMode.USER, actor_id="x", roles=[], capabilities=[]
        )
        assert_session_accepts_messages(session)  # no exception

    def test_revoked_session_raises(self):
        sess_store = InMemorySessionStore()
        session = build_session(
            mode=AssistantMode.USER, actor_id="x", roles=[], capabilities=[]
        )
        sess_store.create(session)
        revoked = sess_store.revoke(session.session_id)
        with pytest.raises(SessionRejectedError, match="revoked"):
            assert_session_accepts_messages(revoked)

    def test_expired_session_raises(self):
        from dataclasses import replace as dc_replace

        session = build_session(
            mode=AssistantMode.USER, actor_id="x", roles=[], capabilities=[]
        )
        expired_session = dc_replace(
            session,
            expires_at=(datetime.now(timezone.utc) - timedelta(seconds=5))
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z"),
        )
        with pytest.raises(SessionRejectedError, match="expired"):
            assert_session_accepts_messages(expired_session)


# ---------------------------------------------------------------------------
# Unit: mode_policy
# ---------------------------------------------------------------------------


class TestValidateSessionRequest:
    def test_user_mode_no_requirements(self):
        validate_session_request(
            mode=AssistantMode.USER,
            reason=None,
            ttl_seconds=None,
            capabilities=[],
        )

    def test_kernel_requires_reason(self):
        with pytest.raises(ModePolicyViolation, match="reason"):
            validate_session_request(
                mode=AssistantMode.KERNEL_DEBUG,
                reason=None,
                ttl_seconds=DEFAULT_KERNEL_TTL_SECONDS,
                capabilities=["assistant.kernel"],
            )

    def test_kernel_empty_reason_fails(self):
        with pytest.raises(ModePolicyViolation, match="reason"):
            validate_session_request(
                mode=AssistantMode.KERNEL_DEBUG,
                reason="   ",
                ttl_seconds=DEFAULT_KERNEL_TTL_SECONDS,
                capabilities=["assistant.kernel"],
            )

    def test_kernel_requires_positive_ttl(self):
        with pytest.raises(ModePolicyViolation, match="TTL"):
            validate_session_request(
                mode=AssistantMode.KERNEL_DEBUG,
                reason="debugging",
                ttl_seconds=0,
                capabilities=["assistant.kernel"],
            )

    def test_kernel_ttl_exceeds_max(self):
        with pytest.raises(ModePolicyViolation, match="maximum"):
            validate_session_request(
                mode=AssistantMode.KERNEL_DEBUG,
                reason="debugging",
                ttl_seconds=MAX_KERNEL_TTL_SECONDS + 1,
                capabilities=["assistant.kernel"],
            )

    def test_kernel_requires_capability(self):
        with pytest.raises(ModePolicyViolation, match="capability"):
            validate_session_request(
                mode=AssistantMode.KERNEL_DEBUG,
                reason="debugging",
                ttl_seconds=DEFAULT_KERNEL_TTL_SECONDS,
                capabilities=["some.other.cap"],
            )

    def test_kernel_observe_also_requires_policy(self):
        with pytest.raises(ModePolicyViolation):
            validate_session_request(
                mode=AssistantMode.KERNEL_OBSERVE,
                reason=None,
                ttl_seconds=DEFAULT_KERNEL_TTL_SECONDS,
                capabilities=["assistant.kernel"],
            )

    def test_kernel_valid_passes(self):
        validate_session_request(
            mode=AssistantMode.KERNEL_DEBUG,
            reason="investigating stale job",
            ttl_seconds=DEFAULT_KERNEL_TTL_SECONDS,
            capabilities=["assistant.kernel"],
        )

    def test_command_broker_classes_are_mode_scoped(self):
        assert command_classes_for_mode(AssistantMode.USER) == []
        assert not mode_allows_command_broker(AssistantMode.USER)

        observe_classes = command_classes_for_mode(AssistantMode.KERNEL_OBSERVE)
        assert observe_classes == ["health_probe", "repo_status"]

        debug_classes = command_classes_for_mode(AssistantMode.KERNEL_DEBUG)
        assert "code_search" in debug_classes
        assert "file_slice" in debug_classes
        assert "test_run" in debug_classes
        assert "log_read" in debug_classes
        assert "repo_edit" not in debug_classes
        assert mode_allows_command_broker("kernel_debug")


class TestCreateSession:
    def test_user_mode_no_ttl(self):
        identity = _FakeIdentity()
        session = create_session(mode=AssistantMode.USER, actor=identity)
        assert session.mode == AssistantMode.USER
        assert session.expires_at is None
        assert session.status == SessionStatus.ACTIVE

    def test_kernel_mode_default_ttl_applied(self):
        identity = _FakeIdentity(capabilities=["assistant.kernel"])
        session = create_session(
            mode=AssistantMode.KERNEL_DEBUG,
            actor=identity,
            reason="debugging",
        )
        assert session.ttl_seconds == DEFAULT_KERNEL_TTL_SECONDS
        assert session.expires_at is not None

    def test_kernel_mode_custom_ttl(self):
        identity = _FakeIdentity(capabilities=["assistant.kernel"])
        session = create_session(
            mode=AssistantMode.KERNEL_DEBUG,
            actor=identity,
            reason="debugging",
            ttl_seconds=600,
        )
        assert session.ttl_seconds == 600

    def test_kernel_mode_missing_capability_raises(self):
        identity = _FakeIdentity(capabilities=[])
        with pytest.raises(ModePolicyViolation):
            create_session(
                mode=AssistantMode.KERNEL_DEBUG,
                actor=identity,
                reason="debugging",
            )


# ---------------------------------------------------------------------------
# Integration: routes via TestClient
# ---------------------------------------------------------------------------


HEADERS = {"Authorization": "Bearer test-token"}


class TestCreateSessionRoute:
    def test_create_user_session(self):
        client = _make_client()
        resp = client.post(
            "/bff/assistant/sessions", json={"mode": "user"}, headers=HEADERS
        )
        assert resp.status_code == 201
        data = resp.json()["data"]
        assert data["mode"] == "user"
        assert data["status"] == "active"
        assert data["session_id"].startswith("asst_sess_")

    def test_create_kernel_session_requires_capability(self, monkeypatch):
        monkeypatch.setenv("PANTHEON_ASSISTANT_KERNEL_ENABLED", "true")
        client = _make_client(capabilities=[])
        resp = client.post(
            "/bff/assistant/sessions",
            json={"mode": "kernel_debug", "reason": "debugging"},
            headers=HEADERS,
        )
        assert resp.status_code == 422

    def test_create_kernel_session_success(self, monkeypatch):
        monkeypatch.setenv("PANTHEON_ASSISTANT_KERNEL_ENABLED", "true")
        client = _make_kernel_client()
        resp = client.post(
            "/bff/assistant/sessions",
            json={"mode": "kernel_debug", "reason": "investigating issue", "ttl_seconds": 900},
            headers=HEADERS,
        )
        assert resp.status_code == 201
        data = resp.json()["data"]
        assert data["mode"] == "kernel_debug"
        assert data["reason"] == "investigating issue"
        assert data["ttl_seconds"] == 900
        assert data["expires_at"] is not None

    def test_create_kernel_session_without_reason_fails(self, monkeypatch):
        monkeypatch.setenv("PANTHEON_ASSISTANT_KERNEL_ENABLED", "true")
        client = _make_kernel_client()
        resp = client.post(
            "/bff/assistant/sessions",
            json={"mode": "kernel_debug"},
            headers=HEADERS,
        )
        assert resp.status_code == 422

    def test_kernel_policy_violation_uses_bff_details_extra(self, monkeypatch):
        monkeypatch.setenv("PANTHEON_ASSISTANT_KERNEL_ENABLED", "true")

        def _bff_error(
            status_code: int,
            code: Any,
            message: str,
            reason: str,
            *,
            details_extra: Optional[dict] = None,
        ) -> HTTPException:
            return HTTPException(
                status_code=status_code,
                detail={
                    "error": {
                        "code": code.value,
                        "message": message,
                        "details": {"reason": reason, **(details_extra or {})},
                    }
                },
            )

        identity = _FakeIdentity(capabilities=[])

        router = create_assistant_router(
            build_context_pack=lambda *a, **kw: (_ for _ in ()).throw(NotImplementedError),
            extract_identity=lambda _auth: identity,
            require_read_role=lambda _id: None,
            bff_error=_bff_error,
            session_store=InMemorySessionStore(),
            transcript_store=InMemoryTranscriptStore(),
        )
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app, raise_server_exceptions=True)

        resp = client.post(
            "/bff/assistant/sessions",
            json={"mode": "kernel_debug", "reason": "debugging"},
            headers=HEADERS,
        )

        assert resp.status_code == 422
        details = resp.json()["detail"]["error"]["details"]
        assert details["field"] == "capabilities"


class TestControlModeRoutes:
    def test_reviewer_cannot_activate_control_mode(self, monkeypatch):
        monkeypatch.setenv("PANTHEON_ASSISTANT_KERNEL_ENABLED", "true")
        store = ControlModeStore(storage_path="off", initial_passphrase="control phrase ok")
        client = _make_client(
            capabilities=["assistant.kernel.debug"],
            roles=["reviewer"],
            mfa_verified=True,
            control_mode_store=store,
        )

        resp = client.post(
            "/bff/assistant/control-mode/activate",
            json={"passphrase": "control phrase ok", "reason": "debugging test"},
            headers=HEADERS,
        )

        assert resp.status_code == 403

    def test_operator_can_activate_and_status_reports_kernel_mode(self, monkeypatch):
        monkeypatch.setenv("PANTHEON_ASSISTANT_KERNEL_ENABLED", "true")
        store = ControlModeStore(storage_path="off", initial_passphrase="control phrase ok")
        client = _make_control_client(store)

        resp = client.post(
            "/bff/assistant/control-mode/activate",
            json={
                "passphrase": "control phrase ok",
                "reason": "debugging management AI",
                "mode": "kernel_debug",
                "ttlSeconds": 900,
                "idleTtlSeconds": 120,
            },
            headers=HEADERS,
        )

        assert resp.status_code == 202, resp.text
        data = resp.json()["data"]
        assert data["active"] is True
        assert data["mode"] == "kernel_debug"
        assert data["ttlSeconds"] == 900
        assert data["idleTtlSeconds"] == 120
        assert "code_search" in data["commandClasses"]
        assert data["requiresConfirmation"] is True

        status = client.get("/bff/assistant/control-mode", headers=HEADERS)
        assert status.status_code == 200
        assert status.json()["data"]["active"] is True

    def test_activate_requires_mfa(self, monkeypatch):
        monkeypatch.setenv("PANTHEON_ASSISTANT_KERNEL_ENABLED", "true")
        store = ControlModeStore(storage_path="off", initial_passphrase="control phrase ok")
        client = _make_client(
            capabilities=["assistant.kernel.debug"],
            roles=["operator"],
            mfa_verified=False,
            control_mode_store=store,
        )

        resp = client.post(
            "/bff/assistant/control-mode/activate",
            json={"passphrase": "control phrase ok", "reason": "debugging test"},
            headers=HEADERS,
        )

        assert resp.status_code == 403

    def test_activate_rejects_bad_passphrase(self, monkeypatch):
        monkeypatch.setenv("PANTHEON_ASSISTANT_KERNEL_ENABLED", "true")
        store = ControlModeStore(storage_path="off", initial_passphrase="control phrase ok")
        client = _make_control_client(store)

        resp = client.post(
            "/bff/assistant/control-mode/activate",
            json={"passphrase": "wrong phrase", "reason": "debugging test"},
            headers=HEADERS,
        )

        assert resp.status_code == 403

    def test_passphrase_can_be_initialized_and_changed_by_admin_mfa(self, monkeypatch):
        store = ControlModeStore(storage_path="off")
        client = _make_client(
            capabilities=["assistant.kernel.debug"],
            roles=["admin"],
            mfa_verified=True,
            control_mode_store=store,
        )

        init_resp = client.post(
            "/bff/assistant/control-mode/passphrase",
            json={"newPassphrase": "initial control phrase"},
            headers=HEADERS,
        )
        assert init_resp.status_code == 202, init_resp.text
        assert init_resp.json()["data"]["initialized"] is True

        change_resp = client.post(
            "/bff/assistant/control-mode/passphrase",
            json={
                "currentPassphrase": "initial control phrase",
                "newPassphrase": "rotated control phrase",
            },
            headers=HEADERS,
        )
        assert change_resp.status_code == 202, change_resp.text
        assert change_resp.json()["data"]["initialized"] is False

        monkeypatch.setenv("PANTHEON_ASSISTANT_KERNEL_ENABLED", "true")
        activate_resp = client.post(
            "/bff/assistant/control-mode/activate",
            json={"passphrase": "rotated control phrase", "reason": "debugging test"},
            headers=HEADERS,
        )
        assert activate_resp.status_code == 202, activate_resp.text

    def test_rotated_store_passphrase_overrides_env_bootstrap(self, tmp_path, monkeypatch):
        store_path = str(tmp_path / "control-mode.json")
        env_hash = passphrase_hash("bootstrap control phrase")
        store = ControlModeStore(storage_path=store_path, initial_passphrase_hash=env_hash)
        store.set_passphrase(
            current_passphrase="bootstrap control phrase",
            new_passphrase="rotated control phrase",
        )

        monkeypatch.setenv(PASSPHRASE_HASH_ENV, env_hash)
        reloaded = ControlModeStore(storage_path=store_path)
        client = _make_client(
            capabilities=["assistant.kernel.debug"],
            roles=["operator"],
            mfa_verified=True,
            control_mode_store=reloaded,
        )
        monkeypatch.setenv("PANTHEON_ASSISTANT_KERNEL_ENABLED", "true")

        old_resp = client.post(
            "/bff/assistant/control-mode/activate",
            json={"passphrase": "bootstrap control phrase", "reason": "debugging test"},
            headers=HEADERS,
        )
        assert old_resp.status_code == 403

        new_resp = client.post(
            "/bff/assistant/control-mode/activate",
            json={"passphrase": "rotated control phrase", "reason": "debugging test"},
            headers=HEADERS,
        )
        assert new_resp.status_code == 202, new_resp.text


class TestGetSessionRoute:
    def test_get_existing_session(self):
        client = _make_client()
        create_resp = client.post(
            "/bff/assistant/sessions", json={"mode": "user"}, headers=HEADERS
        )
        session_id = create_resp.json()["data"]["session_id"]

        get_resp = client.get(
            f"/bff/assistant/sessions/{session_id}", headers=HEADERS
        )
        assert get_resp.status_code == 200
        assert get_resp.json()["data"]["session_id"] == session_id

    def test_get_missing_session_returns_404(self):
        client = _make_client()
        resp = client.get("/bff/assistant/sessions/no_such_id", headers=HEADERS)
        assert resp.status_code == 404


class TestRevokeSessionRoute:
    def test_revoke_active_session(self):
        client = _make_client()
        create_resp = client.post(
            "/bff/assistant/sessions", json={"mode": "user"}, headers=HEADERS
        )
        session_id = create_resp.json()["data"]["session_id"]

        revoke_resp = client.post(
            f"/bff/assistant/sessions/{session_id}/revoke",
            json={"reason": "test done"},
            headers=HEADERS,
        )
        assert revoke_resp.status_code == 200
        data = revoke_resp.json()["data"]
        assert data["status"] == "revoked"
        assert data["revoke_reason"] == "test done"
        assert data["revoked_at"] is not None

    def test_revoke_missing_session_returns_404(self):
        client = _make_client()
        resp = client.post(
            "/bff/assistant/sessions/no_such/revoke",
            json={},
            headers=HEADERS,
        )
        assert resp.status_code == 404


class TestTranscriptRoute:
    def _create_session(self, client: TestClient) -> str:
        resp = client.post(
            "/bff/assistant/sessions", json={"mode": "user"}, headers=HEADERS
        )
        return resp.json()["data"]["session_id"]

    def test_append_and_readback(self):
        client = _make_client()
        sid = self._create_session(client)

        client.post(
            f"/bff/assistant/sessions/{sid}/transcript",
            json={"role": "user", "content": "What is the current loop status?"},
            headers=HEADERS,
        )
        client.post(
            f"/bff/assistant/sessions/{sid}/transcript",
            json={
                "role": "assistant",
                "content": "The loop is active.",
                "context_pack_id": "ctx_abc",
                "provider_run_id": "run_001",
            },
            headers=HEADERS,
        )

        resp = client.get(
            f"/bff/assistant/sessions/{sid}/transcript", headers=HEADERS
        )
        assert resp.status_code == 200
        turns = resp.json()["data"]
        assert len(turns) == 2
        assert turns[0]["role"] == "user"
        assert turns[1]["role"] == "assistant"
        assert turns[1]["context_pack_id"] == "ctx_abc"
        assert turns[1]["provider_run_id"] == "run_001"

    def test_append_stores_source_refs(self):
        client = _make_client()
        sid = self._create_session(client)

        source_refs = [{"source_id": "control_room", "href": "/bff/v5/control-room"}]
        resp = client.post(
            f"/bff/assistant/sessions/{sid}/transcript",
            json={"role": "user", "content": "check status", "source_refs": source_refs},
            headers=HEADERS,
        )
        assert resp.status_code == 201
        turn = resp.json()["data"]
        assert turn["source_refs"] == source_refs

    def test_append_to_revoked_session_returns_409(self):
        client = _make_client()
        sid = self._create_session(client)
        client.post(
            f"/bff/assistant/sessions/{sid}/revoke", json={}, headers=HEADERS
        )

        resp = client.post(
            f"/bff/assistant/sessions/{sid}/transcript",
            json={"role": "user", "content": "should be rejected"},
            headers=HEADERS,
        )
        assert resp.status_code == 409

    def test_append_to_expired_session_returns_409(self):
        from dataclasses import replace as dc_replace

        # Use the store directly to inject an expired session
        session_store = InMemorySessionStore()
        transcript_store = InMemoryTranscriptStore()

        identity = _FakeIdentity()

        def _extract_identity(_auth):
            return identity

        def _require_read_role(_id):
            pass

        router = create_assistant_router(
            build_context_pack=lambda *a, **kw: (_ for _ in ()).throw(NotImplementedError),
            extract_identity=_extract_identity,
            require_read_role=_require_read_role,
            session_store=session_store,
            transcript_store=transcript_store,
        )
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app, raise_server_exceptions=True)

        # Create a session and then manually expire it in the store
        resp = client.post(
            "/bff/assistant/sessions", json={"mode": "user"}, headers=HEADERS
        )
        sid = resp.json()["data"]["session_id"]
        base_session = session_store.get(sid)
        expired = dc_replace(
            base_session,
            expires_at=(datetime.now(timezone.utc) - timedelta(seconds=5))
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z"),
        )
        session_store._sessions[sid] = expired

        post_resp = client.post(
            f"/bff/assistant/sessions/{sid}/transcript",
            json={"role": "user", "content": "this should fail"},
            headers=HEADERS,
        )
        assert post_resp.status_code == 409

    def test_transcript_missing_session_returns_404(self):
        client = _make_client()
        resp = client.get(
            "/bff/assistant/sessions/no_such_id/transcript", headers=HEADERS
        )
        assert resp.status_code == 404

    def test_transcript_empty_on_new_session(self):
        client = _make_client()
        sid = self._create_session(client)
        resp = client.get(
            f"/bff/assistant/sessions/{sid}/transcript", headers=HEADERS
        )
        assert resp.status_code == 200
        assert resp.json()["data"] == []
        assert resp.json()["meta"]["count"] == 0
