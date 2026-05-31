"""Assistant session and transcript store.

Manages in-process session lifecycle (create, expire, revoke) and
transcript persistence (user/assistant turns with context and provider refs).

Storage is in-process and not shared across BFF workers.  Production deployments
should replace InMemorySessionStore with a Redis- or DB-backed implementation
that satisfies the same SessionStore / TranscriptStore protocols.
"""
from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Protocol

from .models import AssistantMode


# ---------------------------------------------------------------------------
# Public data models
# ---------------------------------------------------------------------------


class SessionStatus(str, Enum):
    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"


class TurnRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


@dataclass
class AssistantSession:
    session_id: str
    mode: AssistantMode
    actor_id: str
    roles: List[str]
    capabilities: List[str]
    created_at: str
    expires_at: Optional[str]
    status: SessionStatus
    reason: Optional[str]
    ttl_seconds: Optional[int]
    context_pack_id: Optional[str] = None
    provider_run_id: Optional[str] = None
    audit_refs: List[str] = field(default_factory=list)
    revoked_at: Optional[str] = None
    revoke_reason: Optional[str] = None


@dataclass
class TranscriptTurn:
    turn_id: str
    session_id: str
    role: TurnRole
    content: str
    created_at: str
    context_pack_id: Optional[str] = None
    provider_run_id: Optional[str] = None
    source_refs: List[Dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Store protocols
# ---------------------------------------------------------------------------


class SessionNotFoundError(KeyError):
    """Raised when a session_id is not found in the store."""


class SessionRejectedError(RuntimeError):
    """Raised when an operation is rejected because the session is expired or revoked."""


class SessionStore(Protocol):
    def create(self, session: AssistantSession) -> AssistantSession: ...
    def get(self, session_id: str) -> AssistantSession: ...
    def revoke(self, session_id: str, *, reason: Optional[str] = None) -> AssistantSession: ...
    def update_context(
        self,
        session_id: str,
        *,
        context_pack_id: Optional[str] = None,
        provider_run_id: Optional[str] = None,
        audit_ref: Optional[str] = None,
    ) -> AssistantSession: ...


class TranscriptStore(Protocol):
    def append(self, turn: TranscriptTurn) -> TranscriptTurn: ...
    def list_turns(self, session_id: str) -> List[TranscriptTurn]: ...


# ---------------------------------------------------------------------------
# In-process implementations
# ---------------------------------------------------------------------------


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _is_expired(session: AssistantSession) -> bool:
    if session.expires_at is None:
        return False
    try:
        expires = datetime.fromisoformat(session.expires_at.replace("Z", "+00:00"))
        return datetime.now(timezone.utc) >= expires
    except (ValueError, AttributeError):
        return False


class InMemorySessionStore:
    """Thread-safe in-process session store."""

    def __init__(self) -> None:
        self._sessions: Dict[str, AssistantSession] = {}
        self._lock = threading.Lock()

    def create(self, session: AssistantSession) -> AssistantSession:
        with self._lock:
            self._sessions[session.session_id] = session
        return session

    def get(self, session_id: str) -> AssistantSession:
        with self._lock:
            session = self._sessions.get(session_id)
        if session is None:
            raise SessionNotFoundError(f"Session not found: {session_id!r}")
        # Lazily mark expired sessions
        if session.status == SessionStatus.ACTIVE and _is_expired(session):
            session = self._mark_expired(session_id)
        return session

    def _mark_expired(self, session_id: str) -> AssistantSession:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                raise SessionNotFoundError(session_id)
            if session.status == SessionStatus.ACTIVE:
                updated = AssistantSession(
                    session_id=session.session_id,
                    mode=session.mode,
                    actor_id=session.actor_id,
                    roles=session.roles,
                    capabilities=session.capabilities,
                    created_at=session.created_at,
                    expires_at=session.expires_at,
                    status=SessionStatus.EXPIRED,
                    reason=session.reason,
                    ttl_seconds=session.ttl_seconds,
                    context_pack_id=session.context_pack_id,
                    provider_run_id=session.provider_run_id,
                    audit_refs=list(session.audit_refs),
                    revoked_at=session.revoked_at,
                    revoke_reason=session.revoke_reason,
                )
                self._sessions[session_id] = updated
                return updated
        return session

    def revoke(self, session_id: str, *, reason: Optional[str] = None) -> AssistantSession:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                raise SessionNotFoundError(f"Session not found: {session_id!r}")
            if session.status == SessionStatus.REVOKED:
                return session
            updated = AssistantSession(
                session_id=session.session_id,
                mode=session.mode,
                actor_id=session.actor_id,
                roles=session.roles,
                capabilities=session.capabilities,
                created_at=session.created_at,
                expires_at=session.expires_at,
                status=SessionStatus.REVOKED,
                reason=session.reason,
                ttl_seconds=session.ttl_seconds,
                context_pack_id=session.context_pack_id,
                provider_run_id=session.provider_run_id,
                audit_refs=list(session.audit_refs),
                revoked_at=_utc_now(),
                revoke_reason=reason,
            )
            self._sessions[session_id] = updated
        return updated

    def update_context(
        self,
        session_id: str,
        *,
        context_pack_id: Optional[str] = None,
        provider_run_id: Optional[str] = None,
        audit_ref: Optional[str] = None,
    ) -> AssistantSession:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                raise SessionNotFoundError(f"Session not found: {session_id!r}")
            audit_refs = list(session.audit_refs)
            if audit_ref and audit_ref not in audit_refs:
                audit_refs.append(audit_ref)
            updated = AssistantSession(
                session_id=session.session_id,
                mode=session.mode,
                actor_id=session.actor_id,
                roles=session.roles,
                capabilities=session.capabilities,
                created_at=session.created_at,
                expires_at=session.expires_at,
                status=session.status,
                reason=session.reason,
                ttl_seconds=session.ttl_seconds,
                context_pack_id=context_pack_id if context_pack_id is not None else session.context_pack_id,
                provider_run_id=provider_run_id if provider_run_id is not None else session.provider_run_id,
                audit_refs=audit_refs,
                revoked_at=session.revoked_at,
                revoke_reason=session.revoke_reason,
            )
            self._sessions[session_id] = updated
        return updated


class InMemoryTranscriptStore:
    """Thread-safe in-process transcript store."""

    def __init__(self) -> None:
        self._turns: Dict[str, List[TranscriptTurn]] = {}
        self._lock = threading.Lock()

    def append(self, turn: TranscriptTurn) -> TranscriptTurn:
        with self._lock:
            turns = self._turns.setdefault(turn.session_id, [])
            turns.append(turn)
        return turn

    def list_turns(self, session_id: str) -> List[TranscriptTurn]:
        with self._lock:
            return list(self._turns.get(session_id, []))


# ---------------------------------------------------------------------------
# Factory helpers
# ---------------------------------------------------------------------------


def build_session(
    *,
    mode: AssistantMode,
    actor_id: str,
    roles: List[str],
    capabilities: List[str],
    reason: Optional[str] = None,
    ttl_seconds: Optional[int] = None,
) -> AssistantSession:
    """Create an AssistantSession value object (not yet persisted)."""
    from datetime import timedelta

    now = datetime.now(timezone.utc).replace(microsecond=0)
    created_at = now.isoformat().replace("+00:00", "Z")

    if ttl_seconds is not None and ttl_seconds > 0:
        expires_at = (now + timedelta(seconds=ttl_seconds)).isoformat().replace("+00:00", "Z")
    else:
        expires_at = None

    return AssistantSession(
        session_id=f"asst_sess_{uuid.uuid4().hex[:16]}",
        mode=mode,
        actor_id=actor_id,
        roles=roles,
        capabilities=capabilities,
        created_at=created_at,
        expires_at=expires_at,
        status=SessionStatus.ACTIVE,
        reason=reason,
        ttl_seconds=ttl_seconds,
    )


def build_turn(
    *,
    session_id: str,
    role: TurnRole,
    content: str,
    context_pack_id: Optional[str] = None,
    provider_run_id: Optional[str] = None,
    source_refs: Optional[List[Dict[str, Any]]] = None,
) -> TranscriptTurn:
    """Create a TranscriptTurn value object (not yet persisted)."""
    return TranscriptTurn(
        turn_id=f"turn_{uuid.uuid4().hex[:12]}",
        session_id=session_id,
        role=role,
        content=content,
        created_at=_utc_now(),
        context_pack_id=context_pack_id,
        provider_run_id=provider_run_id,
        source_refs=list(source_refs or []),
    )


def assert_session_accepts_messages(session: AssistantSession) -> None:
    """Raise SessionRejectedError if session is expired or revoked."""
    if session.status == SessionStatus.EXPIRED or _is_expired(session):
        raise SessionRejectedError(
            f"Session {session.session_id!r} is expired and cannot accept new messages."
        )
    if session.status == SessionStatus.REVOKED:
        raise SessionRejectedError(
            f"Session {session.session_id!r} has been revoked and cannot accept new messages."
        )
