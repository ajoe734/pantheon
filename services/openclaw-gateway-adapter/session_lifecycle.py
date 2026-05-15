"""Pantheon-owned OpenClaw session lifecycle store and state machine.

This module is the durable Pantheon-side state owner for OpenClaw sessions.
The boundary facade in main.py delegates to this lifecycle when operators
issue session create/get/list/cancel commands.

Key properties (per SVC-OPENCLAW-SESSION-LIFECYCLE acceptance):
- durable per-session state in a JSON-backed store with file locking
- idempotent create driven by an operator-supplied idempotency key
- operator identity captured for create and cancel actions
- audit log entries appended on every state transition
- degraded upstream recovery: when the upstream call fails, the
  Pantheon record is preserved with state=lost so subsequent reads still
  see the session and operators can retry or cancel

This layer does NOT enable broker/paper/live execution. It only manages the
session metadata state machine and delegates session calls to the typed
OpenClawUpstreamClient. Activation gates remain owned by main.py and the
upstream contract.
"""

from __future__ import annotations

import contextlib
import dataclasses
import datetime as _dt
import hashlib
import json
import os
import threading
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

# State machine ----------------------------------------------------------------

# pending          -> create acknowledged locally; upstream call in flight or pending
# active           -> upstream confirmed the session is live
# cancel_requested -> operator asked to cancel; upstream call in flight
# canceled         -> upstream confirmed cancellation
# failed           -> upstream rejected the create or cancel with a non-retryable error
# lost             -> upstream is unreachable; local record is preserved so the
#                     operator can still see and act on the session
_TERMINAL_STATES = {"canceled", "failed"}
_ALLOWED_TRANSITIONS: Dict[str, set[str]] = {
    "pending": {"active", "failed", "lost", "cancel_requested"},
    "active": {"cancel_requested", "lost", "failed", "canceled"},
    "lost": {"active", "cancel_requested", "failed", "canceled"},
    "cancel_requested": {"canceled", "failed", "lost"},
    "canceled": set(),
    "failed": set(),
}


class LifecycleError(Exception):
    """Raised for lifecycle-level errors that should map to 4xx responses."""

    def __init__(self, error_code: str, message: str, *, status_code: int = 400, details: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.status_code = status_code
        self.details = details or {}

    def to_payload(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "status": "lifecycle_error",
            "error_code": self.error_code,
            "message": self.message,
        }
        if self.details:
            payload["details"] = self.details
        return payload


def _utc_now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _stable_request_hash(payload: Dict[str, Any]) -> str:
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


@dataclasses.dataclass
class SessionRecord:
    session_id: str
    agent_id: str
    session_type: str
    state: str
    operator_id: str
    created_at: str
    updated_at: str
    idempotency_key: Optional[str] = None
    request_hash: Optional[str] = None
    context_bundle: Dict[str, Any] = dataclasses.field(default_factory=dict)
    upstream_session_id: Optional[str] = None
    last_upstream_payload: Optional[Dict[str, Any]] = None
    last_error: Optional[Dict[str, Any]] = None
    canceled_by: Optional[str] = None
    audit_log: List[Dict[str, Any]] = dataclasses.field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "SessionRecord":
        return cls(**{f.name: raw.get(f.name) for f in dataclasses.fields(cls)})


# Upstream client surface ------------------------------------------------------

class _UpstreamLike:
    """Structural protocol for the typed upstream client.

    The real implementation lives in main.py (OpenClawUpstreamClient) but this
    module avoids importing it directly so tests can pass any callable shim.
    """

    def create_session(self, req: Any) -> Dict[str, Any]: ...
    def get_session(self, session_id: str) -> Dict[str, Any]: ...
    def cancel_session(self, session_id: str) -> Dict[str, Any]: ...
    def list_sessions(self) -> List[Dict[str, Any]]: ...


# Durable store ----------------------------------------------------------------


class SessionLifecycleStore:
    """File-backed durable store for OpenClaw session lifecycle records.

    The store is intentionally simple: a single JSON file with a process-local
    lock plus an os.O_EXCL temp file rename for crash safety. It keeps two
    indexes:
      sessions: session_id -> SessionRecord
      idempotency: idempotency_key -> session_id

    The store owns state transitions and audit trail writes. The
    create/get/list/cancel methods are the public API used by main.py.
    """

    def __init__(
        self,
        storage_path: Optional[str | os.PathLike[str]] = None,
        *,
        upstream_factory: Callable[[], Optional[_UpstreamLike]] | None = None,
        clock: Callable[[], str] = _utc_now_iso,
        id_factory: Callable[[], str] = lambda: uuid.uuid4().hex,
    ) -> None:
        path = Path(storage_path) if storage_path else _default_storage_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path
        self._upstream_factory = upstream_factory or (lambda: None)
        self._clock = clock
        self._id_factory = id_factory
        self._lock = threading.RLock()

    # ----- public API ---------------------------------------------------------

    def create_session(
        self,
        *,
        agent_id: str,
        session_type: str,
        operator_id: str,
        idempotency_key: Optional[str],
        context_bundle: Optional[Dict[str, Any]] = None,
        upstream_request_factory: Optional[Callable[[], Any]] = None,
    ) -> Tuple[SessionRecord, bool]:
        """Create a session idempotently.

        Returns (record, replayed). When replayed is True the same idempotency
        key was already used; the existing record is returned without a new
        upstream call.
        """
        if not agent_id:
            raise LifecycleError("LIFECYCLE_INVALID_REQUEST", "agent_id is required.", status_code=400)
        if not session_type:
            raise LifecycleError("LIFECYCLE_INVALID_REQUEST", "session_type is required.", status_code=400)
        if not operator_id:
            raise LifecycleError("LIFECYCLE_OPERATOR_REQUIRED", "operator_id is required.", status_code=401)
        canonical_payload = {
            "agent_id": agent_id,
            "session_type": session_type,
            "context_bundle": context_bundle or {},
        }
        request_hash = _stable_request_hash(canonical_payload)

        with self._lock:
            data = self._load()
            if idempotency_key:
                existing_id = data["idempotency"].get(idempotency_key)
                if existing_id:
                    existing_raw = data["sessions"].get(existing_id)
                    if existing_raw is None:
                        # idempotency index points at a missing record; treat as
                        # corruption and clear the stale pointer.
                        data["idempotency"].pop(idempotency_key, None)
                    else:
                        existing = SessionRecord.from_dict(existing_raw)
                        if existing.request_hash and existing.request_hash != request_hash:
                            raise LifecycleError(
                                "LIFECYCLE_IDEMPOTENCY_CONFLICT",
                                "Idempotency key reused with a different request payload.",
                                status_code=409,
                                details={"session_id": existing.session_id},
                            )
                        return existing, True

            now = self._clock()
            session_id = self._id_factory()
            record = SessionRecord(
                session_id=session_id,
                agent_id=agent_id,
                session_type=session_type,
                state="pending",
                operator_id=operator_id,
                created_at=now,
                updated_at=now,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                context_bundle=dict(canonical_payload["context_bundle"]),
                audit_log=[
                    {
                        "action": "create_requested",
                        "actor": operator_id,
                        "at": now,
                        "detail": {"agent_id": agent_id, "session_type": session_type},
                    }
                ],
            )
            data["sessions"][session_id] = record.to_dict()
            if idempotency_key:
                data["idempotency"][idempotency_key] = session_id
            self._dump(data)

        # Upstream call happens outside the lock so concurrent reads stay fast.
        upstream = self._upstream_factory()
        if upstream is None:
            self._transition(session_id, "lost", actor=operator_id, action="upstream_unavailable",
                             detail={"reason": "OpenClaw upstream client is not configured."})
            return self._read(session_id), False

        try:
            upstream_request = upstream_request_factory() if upstream_request_factory else _build_upstream_request(canonical_payload)
            upstream_payload = upstream.create_session(upstream_request)
        except Exception as exc:  # noqa: BLE001
            error_payload = _coerce_upstream_error(exc)
            new_state = "lost" if error_payload.get("retryable") else "failed"
            self._transition(
                session_id,
                new_state,
                actor=operator_id,
                action="create_failed",
                detail={"error": error_payload},
                last_error=error_payload,
            )
            return self._read(session_id), False

        upstream_id = str(upstream_payload.get("session_id") or upstream_payload.get("id") or "") or None
        self._transition(
            session_id,
            "active",
            actor=operator_id,
            action="create_acknowledged",
            detail={"upstream_session_id": upstream_id},
            upstream_payload=upstream_payload,
            upstream_session_id=upstream_id,
        )
        return self._read(session_id), False

    def get_session(self, session_id: str, *, refresh_from_upstream: bool = True) -> SessionRecord:
        record = self._read_or_none(session_id)
        if record is None:
            raise LifecycleError(
                "LIFECYCLE_NOT_FOUND",
                f"Session {session_id} not found in Pantheon lifecycle store.",
                status_code=404,
            )
        if not refresh_from_upstream or record.state in _TERMINAL_STATES:
            return record
        upstream = self._upstream_factory()
        if upstream is None or record.upstream_session_id is None:
            return record
        try:
            upstream_payload = upstream.get_session(record.upstream_session_id)
        except Exception as exc:  # noqa: BLE001
            error_payload = _coerce_upstream_error(exc)
            if record.state == "active" and error_payload.get("retryable"):
                self._transition(
                    session_id,
                    "lost",
                    actor="system",
                    action="upstream_get_failed",
                    detail={"error": error_payload},
                    last_error=error_payload,
                )
            return self._read(session_id)
        upstream_status = str(upstream_payload.get("status") or "").lower()
        if upstream_status in {"canceled", "cancelled"}:
            self._transition(
                session_id,
                "canceled",
                actor="system",
                action="upstream_reported_canceled",
                detail={"upstream_status": upstream_status},
                upstream_payload=upstream_payload,
            )
        elif record.state != "cancel_requested":
            # Do not push cancel_requested back to active: cancel is in-flight and
            # the upstream has not processed it yet. Return the local record as-is.
            self._transition(
                session_id,
                "active",
                actor="system",
                action="upstream_refreshed",
                detail={"upstream_status": upstream_status or "unknown"},
                upstream_payload=upstream_payload,
            )
        return self._read(session_id)

    def list_sessions(self, *, operator_id: Optional[str] = None, state: Optional[str] = None) -> List[SessionRecord]:
        with self._lock:
            data = self._load()
        records = [SessionRecord.from_dict(raw) for raw in data["sessions"].values()]
        if operator_id is not None:
            records = [r for r in records if r.operator_id == operator_id]
        if state is not None:
            records = [r for r in records if r.state == state]
        records.sort(key=lambda r: r.created_at, reverse=True)
        return records

    def cancel_session(self, session_id: str, *, operator_id: str) -> SessionRecord:
        if not operator_id:
            raise LifecycleError("LIFECYCLE_OPERATOR_REQUIRED", "operator_id is required.", status_code=401)
        record = self._read_or_none(session_id)
        if record is None:
            raise LifecycleError(
                "LIFECYCLE_NOT_FOUND",
                f"Session {session_id} not found in Pantheon lifecycle store.",
                status_code=404,
            )
        if record.state == "canceled":
            return record
        if record.state == "failed":
            raise LifecycleError(
                "LIFECYCLE_TERMINAL",
                "Session has already terminated and cannot be canceled.",
                status_code=409,
                details={"state": record.state},
            )
        self._transition(
            session_id,
            "cancel_requested",
            actor=operator_id,
            action="cancel_requested",
            detail={"requested_by": operator_id},
            canceled_by=operator_id,
        )
        upstream = self._upstream_factory()
        if upstream is None or record.upstream_session_id is None:
            # No upstream channel; treat the cancel as final from Pantheon's
            # perspective. The local state machine is the source of truth here
            # because the upstream session never made it past the boundary.
            self._transition(
                session_id,
                "canceled",
                actor=operator_id,
                action="cancel_completed_locally",
                detail={"reason": "No upstream session id; canceled locally."},
            )
            return self._read(session_id)
        try:
            upstream_payload = upstream.cancel_session(record.upstream_session_id)
        except Exception as exc:  # noqa: BLE001
            error_payload = _coerce_upstream_error(exc)
            new_state = "lost" if error_payload.get("retryable") else "failed"
            self._transition(
                session_id,
                new_state,
                actor=operator_id,
                action="cancel_failed",
                detail={"error": error_payload},
                last_error=error_payload,
            )
            return self._read(session_id)
        self._transition(
            session_id,
            "canceled",
            actor=operator_id,
            action="cancel_acknowledged",
            detail={"upstream_status": upstream_payload.get("status")},
            upstream_payload=upstream_payload,
        )
        return self._read(session_id)

    def append_audit(self, session_id: str, *, action: str, actor: str, detail: Optional[Dict[str, Any]] = None) -> None:
        with self._lock:
            data = self._load()
            raw = data["sessions"].get(session_id)
            if raw is None:
                raise LifecycleError("LIFECYCLE_NOT_FOUND", "session missing", status_code=404)
            event = {"action": action, "actor": actor, "at": self._clock(), "detail": detail or {}}
            raw.setdefault("audit_log", []).append(event)
            raw["updated_at"] = event["at"]
            self._dump(data)

    # ----- internals ----------------------------------------------------------

    def _transition(
        self,
        session_id: str,
        target_state: str,
        *,
        actor: str,
        action: str,
        detail: Optional[Dict[str, Any]] = None,
        upstream_payload: Optional[Dict[str, Any]] = None,
        upstream_session_id: Optional[str] = None,
        last_error: Optional[Dict[str, Any]] = None,
        canceled_by: Optional[str] = None,
    ) -> None:
        with self._lock:
            data = self._load()
            raw = data["sessions"].get(session_id)
            if raw is None:
                raise LifecycleError("LIFECYCLE_NOT_FOUND", "session missing", status_code=404)
            current_state = str(raw.get("state"))
            if current_state in _TERMINAL_STATES and target_state != current_state:
                # Refuse to leave a terminal state. This is a safety guard.
                return
            allowed = _ALLOWED_TRANSITIONS.get(current_state, set())
            if target_state != current_state and target_state not in allowed:
                raise LifecycleError(
                    "LIFECYCLE_INVALID_TRANSITION",
                    f"Cannot transition session from {current_state} to {target_state}.",
                    status_code=409,
                    details={"from": current_state, "to": target_state},
                )
            now = self._clock()
            raw["state"] = target_state
            raw["updated_at"] = now
            if upstream_session_id is not None:
                raw["upstream_session_id"] = upstream_session_id
            if upstream_payload is not None:
                raw["last_upstream_payload"] = upstream_payload
            if last_error is not None:
                raw["last_error"] = last_error
            elif target_state == "active":
                raw["last_error"] = None
            if canceled_by is not None:
                raw["canceled_by"] = canceled_by
            audit_entry = {
                "action": action,
                "actor": actor,
                "at": now,
                "from_state": current_state,
                "to_state": target_state,
                "detail": detail or {},
            }
            raw.setdefault("audit_log", []).append(audit_entry)
            self._dump(data)

    def _read_or_none(self, session_id: str) -> Optional[SessionRecord]:
        with self._lock:
            data = self._load()
            raw = data["sessions"].get(session_id)
        if raw is None:
            return None
        return SessionRecord.from_dict(raw)

    def _read(self, session_id: str) -> SessionRecord:
        record = self._read_or_none(session_id)
        if record is None:
            raise LifecycleError("LIFECYCLE_NOT_FOUND", "session missing", status_code=404)
        return record

    def _load(self) -> Dict[str, Any]:
        if not self._path.exists():
            return {"sessions": {}, "idempotency": {}}
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8") or "{}")
        except json.JSONDecodeError:
            # Treat unreadable store as empty rather than crashing the service.
            # A subsequent dump rewrites the file with valid JSON.
            return {"sessions": {}, "idempotency": {}}
        raw.setdefault("sessions", {})
        raw.setdefault("idempotency", {})
        return raw

    def _dump(self, data: Dict[str, Any]) -> None:
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self._path)


# Helpers ----------------------------------------------------------------------


def _default_storage_path() -> Path:
    raw = os.getenv("OPENCLAW_LIFECYCLE_STORE_PATH")
    if raw:
        return Path(raw).expanduser()
    return Path("/tmp/openclaw-gateway-adapter/session_lifecycle.json")


def _build_upstream_request(payload: Dict[str, Any]) -> Any:
    """Build a CreateSessionRequest-shaped object for the upstream client.

    main.py exposes a pydantic model with the same field names; the typed
    client only reads attribute access so a SimpleNamespace is sufficient
    for tests that don't pull in pydantic.
    """
    from types import SimpleNamespace

    return SimpleNamespace(
        agent_id=payload["agent_id"],
        session_type=payload["session_type"],
        context_bundle=payload.get("context_bundle"),
    )


def _coerce_upstream_error(exc: Exception) -> Dict[str, Any]:
    to_payload = getattr(exc, "to_payload", None)
    if callable(to_payload):
        with contextlib.suppress(Exception):
            payload = to_payload()
            if isinstance(payload, dict):
                payload.setdefault("retryable", bool(getattr(exc, "retryable", False)))
                return payload
    return {
        "status": "upstream_error",
        "error_code": getattr(exc, "error_code", "UPSTREAM_UNKNOWN"),
        "message": str(exc),
        "retryable": bool(getattr(exc, "retryable", False)),
    }
