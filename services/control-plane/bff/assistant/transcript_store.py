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
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Protocol

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
# Durable Management AI-backed implementations
# ---------------------------------------------------------------------------


class ManagementAiAssistantSessionStore:
    """Assistant SessionStore backed by the Management AI conversation store.

    Management AI conversations remain the durable session identity and turn
    history. This adapter stores assistant-specific mode/status metadata in the
    same store family, while also allowing existing Management AI session ids to
    be read through /bff/assistant/* transcript surfaces.
    """

    def __init__(
        self,
        store: Optional[Any] = None,
        *,
        store_factory: Optional[Callable[[], Any]] = None,
    ) -> None:
        if store is None and store_factory is None:
            raise ValueError("ManagementAiAssistantSessionStore requires a store or store_factory")
        self._store = store
        self._store_factory = store_factory

    def _conversation_store(self) -> Any:
        if self._store_factory is not None:
            return self._store_factory()
        return self._store

    def create(self, session: AssistantSession) -> AssistantSession:
        store = self._conversation_store()
        store.upsert_session(
            session_id=session.session_id,
            owner_id=session.actor_id,
            tenant_id=None,
            now=session.created_at,
            title=session.reason or "Assistant session",
        )
        self._persist(session)
        return session

    def get(self, session_id: str) -> AssistantSession:
        store = self._conversation_store()
        management_session = store.get_session(session_id)
        if management_session is None:
            raise SessionNotFoundError(f"Session not found: {session_id!r}")

        metadata = store.get_assistant_session(session_id)
        session = (
            self._session_from_metadata(metadata)
            if metadata is not None
            else self._session_from_management_session(management_session)
        )
        if session.status == SessionStatus.ACTIVE and _is_expired(session):
            session = replace(session, status=SessionStatus.EXPIRED)
            self._persist(session)
        return session

    def get_for_identity(self, session_id: str, identity: Any) -> AssistantSession:
        session = self.get(session_id)
        actor_id = str(getattr(identity, "operator_id", "") or "")
        roles = set(str(role) for role in (getattr(identity, "roles", []) or []))
        if session.actor_id and session.actor_id != actor_id and "admin" not in roles:
            raise SessionNotFoundError(f"Session not found: {session_id!r}")
        return session

    def revoke(self, session_id: str, *, reason: Optional[str] = None) -> AssistantSession:
        session = self.get(session_id)
        if session.status == SessionStatus.REVOKED:
            return session
        updated = replace(
            session,
            status=SessionStatus.REVOKED,
            revoked_at=_utc_now(),
            revoke_reason=reason,
        )
        self._persist(updated)
        return updated

    def update_context(
        self,
        session_id: str,
        *,
        context_pack_id: Optional[str] = None,
        provider_run_id: Optional[str] = None,
        audit_ref: Optional[str] = None,
    ) -> AssistantSession:
        session = self.get(session_id)
        audit_refs = list(session.audit_refs or [])
        if audit_ref and audit_ref not in audit_refs:
            audit_refs.append(audit_ref)
        updated = replace(
            session,
            context_pack_id=context_pack_id if context_pack_id is not None else session.context_pack_id,
            provider_run_id=provider_run_id if provider_run_id is not None else session.provider_run_id,
            audit_refs=audit_refs,
        )
        self._persist(updated)
        return updated

    def _persist(self, session: AssistantSession) -> None:
        self._conversation_store().upsert_assistant_session(
            session_id=session.session_id,
            mode=session.mode.value,
            actor_id=session.actor_id,
            roles=list(session.roles or []),
            capabilities=list(session.capabilities or []),
            created_at=session.created_at,
            expires_at=session.expires_at,
            status=session.status.value,
            reason=session.reason,
            ttl_seconds=session.ttl_seconds,
            context_pack_id=session.context_pack_id,
            provider_run_id=session.provider_run_id,
            audit_refs=list(session.audit_refs or []),
            revoked_at=session.revoked_at,
            revoke_reason=session.revoke_reason,
            updated_at=_utc_now(),
        )

    def _session_from_metadata(self, metadata: Dict[str, Any]) -> AssistantSession:
        mode = _coerce_mode(metadata.get("mode"))
        status = _coerce_status(metadata.get("status"))
        return AssistantSession(
            session_id=str(metadata.get("sessionId") or metadata.get("session_id") or ""),
            mode=mode,
            actor_id=str(metadata.get("actorId") or metadata.get("actor_id") or ""),
            roles=_coerce_str_list(metadata.get("roles")),
            capabilities=_coerce_str_list(metadata.get("capabilities")),
            created_at=str(metadata.get("createdAt") or metadata.get("created_at") or _utc_now()),
            expires_at=_optional_str(metadata.get("expiresAt") or metadata.get("expires_at")),
            status=status,
            reason=_optional_str(metadata.get("reason")),
            ttl_seconds=_optional_int(metadata.get("ttlSeconds") if "ttlSeconds" in metadata else metadata.get("ttl_seconds")),
            context_pack_id=_optional_str(
                metadata.get("contextPackId") or metadata.get("context_pack_id")
            ),
            provider_run_id=_optional_str(
                metadata.get("providerRunId") or metadata.get("provider_run_id")
            ),
            audit_refs=_coerce_str_list(metadata.get("auditRefs") or metadata.get("audit_refs")),
            revoked_at=_optional_str(metadata.get("revokedAt") or metadata.get("revoked_at")),
            revoke_reason=_optional_str(
                metadata.get("revokeReason") or metadata.get("revoke_reason")
            ),
        )

    def _session_from_management_session(self, session: Dict[str, Any]) -> AssistantSession:
        created_at = str(
            session.get("createdAt")
            or session.get("created_at")
            or session.get("updatedAt")
            or session.get("updated_at")
            or _utc_now()
        )
        return AssistantSession(
            session_id=str(session.get("sessionId") or session.get("session_id") or session.get("id") or ""),
            mode=AssistantMode.USER,
            actor_id=str(session.get("ownerId") or session.get("owner_id") or ""),
            roles=[],
            capabilities=[],
            created_at=created_at,
            expires_at=None,
            status=SessionStatus.ACTIVE,
            reason=_optional_str(session.get("title")),
            ttl_seconds=None,
        )


class ManagementAiAssistantTranscriptStore:
    """TranscriptStore that aliases assistant turns to Management AI turns."""

    def __init__(
        self,
        store: Optional[Any] = None,
        *,
        store_factory: Optional[Callable[[], Any]] = None,
    ) -> None:
        if store is None and store_factory is None:
            raise ValueError("ManagementAiAssistantTranscriptStore requires a store or store_factory")
        self._store = store
        self._store_factory = store_factory

    def _conversation_store(self) -> Any:
        if self._store_factory is not None:
            return self._store_factory()
        return self._store

    def append(self, turn: TranscriptTurn) -> TranscriptTurn:
        metadata = {
            "context_pack_id": turn.context_pack_id,
            "provider_run_id": turn.provider_run_id,
            "source_refs": list(turn.source_refs or []),
        }
        row = self._conversation_store().append_turn(
            turn_id=turn.turn_id,
            session_id=turn.session_id,
            role=turn.role.value if hasattr(turn.role, "value") else str(turn.role),
            text=turn.content,
            created_at=turn.created_at,
            trace_id=turn.provider_run_id,
            assistant_metadata=metadata,
        )
        return self._turn_from_management_turn(row)

    def list_turns(self, session_id: str) -> List[TranscriptTurn]:
        return [
            self._turn_from_management_turn(turn)
            for turn in self._conversation_store().list_turns(session_id)
        ]

    def _turn_from_management_turn(self, turn: Dict[str, Any]) -> TranscriptTurn:
        metadata = turn.get("assistantMetadata")
        if not isinstance(metadata, dict):
            metadata = turn.get("assistant_metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        source_refs = metadata.get("source_refs") or metadata.get("sourceRefs") or []
        if not isinstance(source_refs, list):
            source_refs = []
        return TranscriptTurn(
            turn_id=str(turn.get("turn_id") or turn.get("turnId") or turn.get("id") or ""),
            session_id=str(turn.get("session_id") or turn.get("sessionId") or ""),
            role=_coerce_turn_role(turn.get("role")),
            content=str(turn.get("content") if turn.get("content") is not None else turn.get("text") or ""),
            created_at=str(turn.get("created_at") or turn.get("createdAt") or _utc_now()),
            context_pack_id=_optional_str(
                metadata.get("context_pack_id") or metadata.get("contextPackId")
            ),
            provider_run_id=_optional_str(
                metadata.get("provider_run_id") or metadata.get("providerRunId")
            ),
            source_refs=[ref for ref in source_refs if isinstance(ref, dict)],
        )


def _coerce_mode(value: Any) -> AssistantMode:
    try:
        return AssistantMode(str(value or AssistantMode.USER.value))
    except ValueError:
        return AssistantMode.USER


def _coerce_status(value: Any) -> SessionStatus:
    try:
        return SessionStatus(str(value or SessionStatus.ACTIVE.value))
    except ValueError:
        return SessionStatus.ACTIVE


def _coerce_turn_role(value: Any) -> TurnRole:
    try:
        return TurnRole(str(value or TurnRole.USER.value))
    except ValueError:
        return TurnRole.USER


def _coerce_str_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _optional_str(value: Any) -> Optional[str]:
    if value in (None, ""):
        return None
    return str(value)


def _optional_int(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


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
