from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Protocol

from services.foundation.persistence_posture import validate_persistence_posture
from services.foundation.postgres_json_store import (
    PostgresJsonOwnerStore,
    ensure_postgres_schema,
    quote_pg_identifier,
)


BACKEND_ENV = "MANAGEMENT_AI_STORE_BACKEND"
DSN_ENV = "MANAGEMENT_AI_STORE_DSN"
DATABASE_URL_ENV = "MANAGEMENT_AI_DATABASE_URL"
SCHEMA_ENV = "MANAGEMENT_AI_STORE_SCHEMA"
SURFACE_ENV = "MANAGEMENT_AI_STORE_SURFACE"
OWNER_SERVICE_ENV = "MANAGEMENT_AI_STORE_OWNER_SERVICE"

DEFAULT_BACKEND = "json"
DEFAULT_SCHEMA = "management_ai"
DEFAULT_SURFACE = "assistant_conversation"
DEFAULT_OWNER_SERVICE = "operator-bff"


def storage_disabled(value: Optional[str]) -> bool:
    raw = str(value or "").strip()
    return raw.lower() in {"off", "false", "disabled", "none", ":memory:"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _json_clone(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))


def _json_dumps(value: Any) -> str:
    return json.dumps(value if value is not None else None, ensure_ascii=False, sort_keys=True)


def _json_loads(value: Any, fallback: Any) -> Any:
    if value in (None, ""):
        return fallback
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except json.JSONDecodeError:
        return fallback


def _optional_str(value: Any) -> Optional[str]:
    if value in (None, ""):
        return None
    return str(value)


def _timestamp(value: Any) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return str(value or _utc_now())


def _safe_int(value: Any, fallback: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _session_payload(
    *,
    session_id: str,
    owner_id: str,
    tenant_id: Optional[str],
    title: Optional[str],
    created_at: str,
    updated_at: str,
) -> Dict[str, Any]:
    clean_session_id = str(session_id or "").strip()
    clean_owner_id = str(owner_id or "")
    clean_tenant_id = str(tenant_id or "") or None
    return {
        "id": clean_session_id,
        "sessionId": clean_session_id,
        "session_id": clean_session_id,
        "ownerId": clean_owner_id,
        "owner_id": clean_owner_id,
        "tenantId": clean_tenant_id,
        "tenant_id": clean_tenant_id,
        "title": str(title or ""),
        "createdAt": str(created_at or updated_at or _utc_now()),
        "created_at": str(created_at or updated_at or _utc_now()),
        "updatedAt": str(updated_at or created_at or _utc_now()),
        "updated_at": str(updated_at or created_at or _utc_now()),
    }


def _turn_payload(
    *,
    turn_id: str,
    session_id: str,
    role: str,
    text: str,
    created_at: str,
    sequence: int,
    trace_id: Optional[str] = None,
    attachments: Optional[List[Dict[str, Any]]] = None,
    provider_status: Optional[Dict[str, Any]] = None,
    ui_snapshot: Optional[Dict[str, Any]] = None,
    ui_actions: Optional[List[Dict[str, Any]]] = None,
    assistant_metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    clean_turn_id = str(turn_id or "").strip()
    clean_session_id = str(session_id or "").strip()
    clean_role = str(role or "").strip().lower()
    if clean_role not in {"user", "assistant", "system"}:
        raise ValueError("assistant conversation turn role must be user, assistant, or system")
    clean_text = str(text or "")
    clean_trace_id = str(trace_id or "").strip() or None
    clean_attachments = list(attachments or [])
    clean_provider_status = dict(provider_status) if isinstance(provider_status, dict) else None
    clean_ui_snapshot = dict(ui_snapshot) if isinstance(ui_snapshot, dict) else None
    clean_ui_actions = list(ui_actions or [])
    clean_assistant_metadata = dict(assistant_metadata) if isinstance(assistant_metadata, dict) else {}
    clean_created_at = _timestamp(created_at)
    return {
        "id": clean_turn_id,
        "turnId": clean_turn_id,
        "turn_id": clean_turn_id,
        "sessionId": clean_session_id,
        "session_id": clean_session_id,
        "role": clean_role,
        "text": clean_text,
        "content": clean_text,
        "attachments": clean_attachments,
        "providerStatus": clean_provider_status,
        "provider_status": clean_provider_status,
        "traceId": clean_trace_id,
        "trace_id": clean_trace_id,
        "uiSnapshot": clean_ui_snapshot,
        "ui_snapshot": clean_ui_snapshot,
        "uiActions": clean_ui_actions,
        "ui_actions": clean_ui_actions,
        "actions": clean_ui_actions,
        "assistantMetadata": clean_assistant_metadata,
        "assistant_metadata": clean_assistant_metadata,
        "createdAt": clean_created_at,
        "created_at": clean_created_at,
        "sequence": int(sequence),
    }


def _assistant_session_payload(
    *,
    session_id: str,
    mode: str,
    actor_id: str,
    roles: List[str],
    capabilities: List[str],
    created_at: str,
    expires_at: Optional[str],
    status: str,
    reason: Optional[str],
    ttl_seconds: Optional[int],
    context_pack_id: Optional[str],
    provider_run_id: Optional[str],
    audit_refs: List[str],
    revoked_at: Optional[str],
    revoke_reason: Optional[str],
    updated_at: Optional[str],
) -> Dict[str, Any]:
    clean_session_id = str(session_id or "").strip()
    clean_updated_at = str(updated_at or created_at or _utc_now())
    clean_ttl = int(ttl_seconds) if ttl_seconds is not None else None
    refs = [str(ref) for ref in list(audit_refs or [])]
    return {
        "sessionId": clean_session_id,
        "session_id": clean_session_id,
        "mode": str(mode or "user"),
        "actorId": str(actor_id or ""),
        "actor_id": str(actor_id or ""),
        "roles": [str(role) for role in list(roles or [])],
        "capabilities": [str(cap) for cap in list(capabilities or [])],
        "createdAt": str(created_at or clean_updated_at),
        "created_at": str(created_at or clean_updated_at),
        "expiresAt": expires_at,
        "expires_at": expires_at,
        "status": str(status or "active"),
        "reason": reason,
        "ttlSeconds": clean_ttl,
        "ttl_seconds": clean_ttl,
        "contextPackId": context_pack_id,
        "context_pack_id": context_pack_id,
        "providerRunId": provider_run_id,
        "provider_run_id": provider_run_id,
        "auditRefs": refs,
        "audit_refs": refs,
        "revokedAt": revoked_at,
        "revoked_at": revoked_at,
        "revokeReason": revoke_reason,
        "revoke_reason": revoke_reason,
        "updatedAt": clean_updated_at,
        "updated_at": clean_updated_at,
    }


class AssistantConversationStoreBackend(Protocol):
    def reset(self) -> None: ...
    def create_session(
        self,
        *,
        session_id: str,
        owner_id: str,
        tenant_id: Optional[str],
        now: str,
        title: Optional[str] = None,
    ) -> Dict[str, Any]: ...
    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]: ...
    def list_sessions(
        self,
        *,
        owner_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]: ...
    def touch_session(self, session_id: str, *, now: str) -> Optional[Dict[str, Any]]: ...
    def append_turn(
        self,
        *,
        turn_id: str,
        session_id: str,
        role: str,
        text: str,
        created_at: str,
        trace_id: Optional[str] = None,
        attachments: Optional[List[Dict[str, Any]]] = None,
        provider_status: Optional[Dict[str, Any]] = None,
        ui_snapshot: Optional[Dict[str, Any]] = None,
        ui_actions: Optional[List[Dict[str, Any]]] = None,
        assistant_metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]: ...
    def list_turns(self, session_id: str) -> List[Dict[str, Any]]: ...
    def list_all_turns(self) -> List[Dict[str, Any]]: ...
    def put_idempotency(self, key: str, *, request_hash: str, result: Dict[str, Any]) -> Dict[str, Any]: ...
    def get_idempotency(self, key: str) -> Optional[Dict[str, Any]]: ...
    def put_assistant_session(self, **kwargs: Any) -> Dict[str, Any]: ...
    def get_assistant_session(self, session_id: str) -> Optional[Dict[str, Any]]: ...


class JsonAssistantConversationStore:
    def __init__(self, *, storage_path: Optional[str] = None) -> None:
        self._storage_path = storage_path
        self._lock = threading.RLock()
        self._data = self._empty_data()
        self._load()

    @staticmethod
    def _empty_data() -> Dict[str, Any]:
        return {
            "sessions": {},
            "turns": [],
            "idempotency": {},
            "assistant_sessions": {},
            "sequence": 0,
        }

    def _enabled(self) -> bool:
        return not storage_disabled(self._storage_path)

    def _path(self) -> Optional[Path]:
        if not self._enabled():
            return None
        return Path(str(self._storage_path))

    def _load(self) -> None:
        path = self._path()
        if path is None or not path.exists():
            return
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if isinstance(loaded, dict):
            data = self._empty_data()
            for key in data:
                if key in loaded:
                    data[key] = loaded[key]
            self._data = data

    def _flush(self) -> None:
        path = self._path()
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
        tmp_path.write_text(json.dumps(self._data, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        os.replace(tmp_path, path)

    def reset(self) -> None:
        with self._lock:
            self._data = self._empty_data()
            self._flush()

    def create_session(
        self,
        *,
        session_id: str,
        owner_id: str,
        tenant_id: Optional[str],
        now: str,
        title: Optional[str] = None,
    ) -> Dict[str, Any]:
        clean_session_id = str(session_id or "").strip()
        if not clean_session_id:
            raise ValueError("session_id is required")
        with self._lock:
            existing = self._data["sessions"].get(clean_session_id)
            if isinstance(existing, dict):
                payload = _session_payload(
                    session_id=clean_session_id,
                    owner_id=str(existing.get("ownerId") or existing.get("owner_id") or owner_id or ""),
                    tenant_id=existing.get("tenantId") or existing.get("tenant_id") or tenant_id,
                    title=existing.get("title") or title or "",
                    created_at=str(existing.get("createdAt") or existing.get("created_at") or now),
                    updated_at=now,
                )
            else:
                payload = _session_payload(
                    session_id=clean_session_id,
                    owner_id=owner_id,
                    tenant_id=tenant_id,
                    title=title,
                    created_at=now,
                    updated_at=now,
                )
            self._data["sessions"][clean_session_id] = payload
            self._flush()
            return _json_clone(payload)

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        clean_session_id = str(session_id or "").strip()
        with self._lock:
            session = self._data["sessions"].get(clean_session_id)
            return _json_clone(session) if isinstance(session, dict) else None

    def list_sessions(
        self,
        *,
        owner_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        clean_owner_id = str(owner_id or "").strip()
        clean_tenant_id = str(tenant_id or "").strip()
        clean_limit = max(1, int(limit or 100))

        def visible(session: Mapping[str, Any]) -> bool:
            session_owner = str(session.get("ownerId") or session.get("owner_id") or "").strip()
            session_tenant = str(session.get("tenantId") or session.get("tenant_id") or "").strip()
            return (
                (clean_owner_id and session_owner == clean_owner_id)
                or (clean_tenant_id and session_tenant == clean_tenant_id)
                or (not clean_owner_id and not clean_tenant_id)
            )

        with self._lock:
            sessions = [
                _json_clone(item)
                for item in self._data["sessions"].values()
                if isinstance(item, dict) and visible(item)
            ]
        return sorted(
            sessions,
            key=lambda item: (
                str(item.get("updatedAt") or item.get("updated_at") or ""),
                str(item.get("createdAt") or item.get("created_at") or ""),
                str(item.get("sessionId") or item.get("session_id") or item.get("id") or ""),
            ),
            reverse=True,
        )[:clean_limit]

    def touch_session(self, session_id: str, *, now: str) -> Optional[Dict[str, Any]]:
        clean_session_id = str(session_id or "").strip()
        with self._lock:
            session = self._data["sessions"].get(clean_session_id)
            if not isinstance(session, dict):
                return None
            session["updatedAt"] = now
            session["updated_at"] = now
            self._flush()
            return _json_clone(session)

    def append_turn(
        self,
        *,
        turn_id: str,
        session_id: str,
        role: str,
        text: str,
        created_at: str,
        trace_id: Optional[str] = None,
        attachments: Optional[List[Dict[str, Any]]] = None,
        provider_status: Optional[Dict[str, Any]] = None,
        ui_snapshot: Optional[Dict[str, Any]] = None,
        ui_actions: Optional[List[Dict[str, Any]]] = None,
        assistant_metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        clean_session_id = str(session_id or "").strip()
        with self._lock:
            if clean_session_id not in self._data["sessions"]:
                raise KeyError(clean_session_id)
            self._data["sequence"] = _safe_int(self._data.get("sequence")) + 1
            turn = _turn_payload(
                turn_id=turn_id,
                session_id=clean_session_id,
                role=role,
                text=text,
                created_at=created_at,
                sequence=self._data["sequence"],
                trace_id=trace_id,
                attachments=attachments,
                provider_status=provider_status,
                ui_snapshot=ui_snapshot,
                ui_actions=ui_actions,
                assistant_metadata=assistant_metadata,
            )
            self._data["turns"].append(turn)
            session = self._data["sessions"][clean_session_id]
            if isinstance(session, dict):
                session["updatedAt"] = turn["createdAt"]
                session["updated_at"] = turn["created_at"]
            self._flush()
            return _json_clone(turn)

    def list_turns(self, session_id: str) -> List[Dict[str, Any]]:
        clean_session_id = str(session_id or "").strip()
        with self._lock:
            turns = [
                _json_clone(item)
                for item in self._data["turns"]
                if isinstance(item, dict)
                and str(item.get("sessionId") or item.get("session_id") or "") == clean_session_id
            ]
        return sorted(
            turns,
            key=lambda item: (
                str(item.get("createdAt") or item.get("created_at") or ""),
                _safe_int(item.get("sequence")),
            ),
        )

    def list_all_turns(self) -> List[Dict[str, Any]]:
        with self._lock:
            turns = [_json_clone(item) for item in self._data["turns"] if isinstance(item, dict)]
        return sorted(
            turns,
            key=lambda item: (
                str(item.get("createdAt") or item.get("created_at") or ""),
                _safe_int(item.get("sequence")),
            ),
        )

    def put_idempotency(self, key: str, *, request_hash: str, result: Dict[str, Any]) -> Dict[str, Any]:
        clean_key = str(key or "").strip()
        if not clean_key:
            raise ValueError("idempotency key is required")
        now = _utc_now()
        with self._lock:
            existing = self._data["idempotency"].get(clean_key)
            created_at = existing.get("createdAt") if isinstance(existing, dict) else now
            payload = {
                "key": clean_key,
                "request_hash": str(request_hash or ""),
                "result": _json_clone(result),
                "createdAt": created_at,
                "created_at": created_at,
                "updatedAt": now,
                "updated_at": now,
            }
            self._data["idempotency"][clean_key] = payload
            self._flush()
            return _json_clone(payload)

    def get_idempotency(self, key: str) -> Optional[Dict[str, Any]]:
        clean_key = str(key or "").strip()
        with self._lock:
            payload = self._data["idempotency"].get(clean_key)
            return _json_clone(payload) if isinstance(payload, dict) else None

    def put_assistant_session(self, **kwargs: Any) -> Dict[str, Any]:
        payload = _assistant_session_payload(**kwargs)
        with self._lock:
            self._data["assistant_sessions"][payload["sessionId"]] = payload
            self._flush()
            return _json_clone(payload)

    def get_assistant_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        clean_session_id = str(session_id or "").strip()
        with self._lock:
            payload = self._data["assistant_sessions"].get(clean_session_id)
            return _json_clone(payload) if isinstance(payload, dict) else None


class PostgresAssistantConversationStore:
    def __init__(
        self,
        *,
        dsn: str,
        schema: str = DEFAULT_SCHEMA,
        surface: str = DEFAULT_SURFACE,
        owner_service: str = DEFAULT_OWNER_SERVICE,
        bootstrap: bool = True,
    ) -> None:
        if not dsn:
            raise ValueError("Postgres DSN is required")
        self.dsn = dsn
        self.schema = str(schema or DEFAULT_SCHEMA).strip()
        self.surface = str(surface or DEFAULT_SURFACE).strip()
        self.owner_service = str(owner_service or DEFAULT_OWNER_SERVICE).strip()
        self.sessions_table_name = f"{self.schema}.{self.surface}_sessions"
        self.turns_table_name = f"{self.schema}.{self.surface}_turns"
        self.idempotency_table_name = f"{self.schema}.{self.surface}_idempotency"
        self.assistant_sessions_table_name = f"{self.schema}.{self.surface}_assistant_sessions"
        self.sessions = PostgresJsonOwnerStore(
            dsn=dsn,
            table=self.sessions_table_name,
            owner_service=self.owner_service,
            bootstrap=bootstrap,
        )
        self.idempotency = PostgresJsonOwnerStore(
            dsn=dsn,
            table=self.idempotency_table_name,
            owner_service=self.owner_service,
            bootstrap=bootstrap,
        )
        self.assistant_sessions = PostgresJsonOwnerStore(
            dsn=dsn,
            table=self.assistant_sessions_table_name,
            owner_service=self.owner_service,
            bootstrap=bootstrap,
        )
        self.turns_table = quote_pg_identifier(self.turns_table_name)
        self.sessions_table = quote_pg_identifier(self.sessions_table_name)
        if bootstrap:
            self.bootstrap()

    def _connect(self):
        try:
            import psycopg  # type: ignore[import]
        except ImportError as exc:
            raise RuntimeError("psycopg is required when MANAGEMENT_AI_STORE_BACKEND=postgres") from exc
        return psycopg.connect(self.dsn)

    def bootstrap(self) -> None:
        index_name = quote_pg_identifier(f"{self.schema}.{self.surface}_turns_session_created_idx")
        with self._connect() as conn:
            ensure_postgres_schema(conn, self.schema)
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self.turns_table} (
                    turn_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES {self.sessions_table}(record_id) ON DELETE CASCADE,
                    role TEXT NOT NULL CHECK (role IN ('user','assistant','system')),
                    text TEXT NOT NULL DEFAULT '',
                    attachments JSONB NOT NULL DEFAULT '[]'::jsonb,
                    provider_status JSONB,
                    trace_id TEXT,
                    ui_snapshot JSONB,
                    ui_actions JSONB NOT NULL DEFAULT '[]'::jsonb,
                    assistant_metadata JSONB,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    sequence BIGSERIAL NOT NULL
                )
                """
            )
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS {index_name} "
                f"ON {self.turns_table} (session_id, created_at, sequence)"
            )

    def reset(self) -> None:
        with self._connect() as conn:
            conn.execute(f"DELETE FROM {self.turns_table}")
        for session in self.sessions.list_all():
            session_id = str(session.get("sessionId") or session.get("session_id") or session.get("id") or "")
            if session_id:
                self.sessions.put(session_id, {"deleted": True, "sessionId": session_id})

    def create_session(
        self,
        *,
        session_id: str,
        owner_id: str,
        tenant_id: Optional[str],
        now: str,
        title: Optional[str] = None,
    ) -> Dict[str, Any]:
        clean_session_id = str(session_id or "").strip()
        if not clean_session_id:
            raise ValueError("session_id is required")
        existing = self.get_session(clean_session_id)
        if existing is not None:
            payload = _session_payload(
                session_id=clean_session_id,
                owner_id=str(existing.get("ownerId") or existing.get("owner_id") or owner_id or ""),
                tenant_id=existing.get("tenantId") or existing.get("tenant_id") or tenant_id,
                title=existing.get("title") or title or "",
                created_at=str(existing.get("createdAt") or existing.get("created_at") or now),
                updated_at=now,
            )
        else:
            payload = _session_payload(
                session_id=clean_session_id,
                owner_id=owner_id,
                tenant_id=tenant_id,
                title=title,
                created_at=now,
                updated_at=now,
            )
        self.sessions.put(clean_session_id, payload)
        return payload

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        clean_session_id = str(session_id or "").strip()
        payload = self.sessions.get(clean_session_id)
        if not isinstance(payload, dict) or payload.get("deleted") is True:
            return None
        return payload

    def list_sessions(
        self,
        *,
        owner_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        clean_owner_id = str(owner_id or "").strip()
        clean_tenant_id = str(tenant_id or "").strip()
        clean_limit = max(1, int(limit or 100))

        def visible(session: Mapping[str, Any]) -> bool:
            if session.get("deleted") is True:
                return False
            session_owner = str(session.get("ownerId") or session.get("owner_id") or "").strip()
            session_tenant = str(session.get("tenantId") or session.get("tenant_id") or "").strip()
            return (
                (clean_owner_id and session_owner == clean_owner_id)
                or (clean_tenant_id and session_tenant == clean_tenant_id)
                or (not clean_owner_id and not clean_tenant_id)
            )

        sessions = [session for session in self.sessions.list_all() if isinstance(session, dict) and visible(session)]
        return sorted(
            sessions,
            key=lambda item: (
                str(item.get("updatedAt") or item.get("updated_at") or ""),
                str(item.get("createdAt") or item.get("created_at") or ""),
                str(item.get("sessionId") or item.get("session_id") or item.get("id") or ""),
            ),
            reverse=True,
        )[:clean_limit]

    def touch_session(self, session_id: str, *, now: str) -> Optional[Dict[str, Any]]:
        payload = self.get_session(session_id)
        if payload is None:
            return None
        payload["updatedAt"] = now
        payload["updated_at"] = now
        self.sessions.put(str(payload.get("sessionId") or payload.get("session_id") or session_id), payload)
        return payload

    def append_turn(
        self,
        *,
        turn_id: str,
        session_id: str,
        role: str,
        text: str,
        created_at: str,
        trace_id: Optional[str] = None,
        attachments: Optional[List[Dict[str, Any]]] = None,
        provider_status: Optional[Dict[str, Any]] = None,
        ui_snapshot: Optional[Dict[str, Any]] = None,
        ui_actions: Optional[List[Dict[str, Any]]] = None,
        assistant_metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        clean_session_id = str(session_id or "").strip()
        if self.get_session(clean_session_id) is None:
            raise KeyError(clean_session_id)
        clean_role = str(role or "").strip().lower()
        if clean_role not in {"user", "assistant", "system"}:
            raise ValueError("assistant conversation turn role must be user, assistant, or system")
        with self._connect() as conn:
            cursor = conn.execute(
                f"""
                INSERT INTO {self.turns_table}
                  (turn_id, session_id, role, text, attachments, provider_status,
                   trace_id, ui_snapshot, ui_actions, assistant_metadata, created_at)
                VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s)
                RETURNING turn_id, session_id, role, text, attachments, provider_status,
                          trace_id, ui_snapshot, ui_actions, assistant_metadata, created_at, sequence
                """,
                (
                    str(turn_id or "").strip(),
                    clean_session_id,
                    clean_role,
                    str(text or ""),
                    _json_dumps(list(attachments or [])),
                    _json_dumps(provider_status) if isinstance(provider_status, dict) else None,
                    str(trace_id or "").strip() or None,
                    _json_dumps(ui_snapshot) if isinstance(ui_snapshot, dict) else None,
                    _json_dumps(list(ui_actions or [])),
                    _json_dumps(assistant_metadata) if isinstance(assistant_metadata, dict) else None,
                    created_at,
                ),
            )
            row = self._fetch_one(cursor)
        payload = self._row_to_turn(row)
        self.touch_session(clean_session_id, now=payload["createdAt"])
        return payload

    def list_turns(self, session_id: str) -> List[Dict[str, Any]]:
        clean_session_id = str(session_id or "").strip()
        with self._connect() as conn:
            cursor = conn.execute(
                f"""
                SELECT turn_id, session_id, role, text, attachments, provider_status,
                       trace_id, ui_snapshot, ui_actions, assistant_metadata, created_at, sequence
                FROM {self.turns_table}
                WHERE session_id = %s
                ORDER BY created_at ASC, sequence ASC
                """,
                (clean_session_id,),
            )
            rows = cursor.fetchall()
        return [self._row_to_turn(row) for row in rows]

    def list_all_turns(self) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            cursor = conn.execute(
                f"""
                SELECT turn_id, session_id, role, text, attachments, provider_status,
                       trace_id, ui_snapshot, ui_actions, assistant_metadata, created_at, sequence
                FROM {self.turns_table}
                ORDER BY created_at ASC, sequence ASC
                """
            )
            rows = cursor.fetchall()
        return [self._row_to_turn(row) for row in rows]

    def put_idempotency(self, key: str, *, request_hash: str, result: Dict[str, Any]) -> Dict[str, Any]:
        clean_key = str(key or "").strip()
        if not clean_key:
            raise ValueError("idempotency key is required")
        now = _utc_now()
        existing = self.get_idempotency(clean_key)
        created_at = existing.get("createdAt") if isinstance(existing, dict) else now
        payload = {
            "key": clean_key,
            "request_hash": str(request_hash or ""),
            "result": result,
            "createdAt": created_at,
            "created_at": created_at,
            "updatedAt": now,
            "updated_at": now,
        }
        self.idempotency.put(clean_key, payload)
        return payload

    def get_idempotency(self, key: str) -> Optional[Dict[str, Any]]:
        payload = self.idempotency.get(str(key or "").strip())
        return payload if isinstance(payload, dict) else None

    def put_assistant_session(self, **kwargs: Any) -> Dict[str, Any]:
        payload = _assistant_session_payload(**kwargs)
        self.assistant_sessions.put(payload["sessionId"], payload)
        return payload

    def get_assistant_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        payload = self.assistant_sessions.get(str(session_id or "").strip())
        return payload if isinstance(payload, dict) else None

    @staticmethod
    def _fetch_one(cursor: Any) -> Any:
        if hasattr(cursor, "fetchone"):
            return cursor.fetchone()
        rows = cursor.fetchall()
        return rows[0] if rows else None

    @staticmethod
    def _row_value(row: Any, index: int, key: str) -> Any:
        if isinstance(row, Mapping):
            return row.get(key)
        return row[index]

    def _row_to_turn(self, row: Any) -> Dict[str, Any]:
        if row is None:
            raise KeyError("turn row not found")
        attachments = _json_loads(self._row_value(row, 4, "attachments"), [])
        provider_status = _json_loads(self._row_value(row, 5, "provider_status"), None)
        ui_snapshot = _json_loads(self._row_value(row, 7, "ui_snapshot"), None)
        ui_actions = _json_loads(self._row_value(row, 8, "ui_actions"), [])
        assistant_metadata = _json_loads(self._row_value(row, 9, "assistant_metadata"), {})
        return _turn_payload(
            turn_id=str(self._row_value(row, 0, "turn_id") or ""),
            session_id=str(self._row_value(row, 1, "session_id") or ""),
            role=str(self._row_value(row, 2, "role") or ""),
            text=str(self._row_value(row, 3, "text") or ""),
            attachments=attachments if isinstance(attachments, list) else [],
            provider_status=provider_status if isinstance(provider_status, dict) else None,
            trace_id=_optional_str(self._row_value(row, 6, "trace_id")),
            ui_snapshot=ui_snapshot if isinstance(ui_snapshot, dict) else None,
            ui_actions=ui_actions if isinstance(ui_actions, list) else [],
            assistant_metadata=assistant_metadata if isinstance(assistant_metadata, dict) else {},
            created_at=_timestamp(self._row_value(row, 10, "created_at")),
            sequence=_safe_int(self._row_value(row, 11, "sequence")),
        )


class AssistantConversationStore:
    def __init__(
        self,
        *,
        backend: Optional[str] = None,
        storage_path: Optional[str] = None,
        dsn: Optional[str] = None,
        schema: Optional[str] = None,
        surface: Optional[str] = None,
        owner_service: Optional[str] = None,
        env: Optional[Mapping[str, str]] = None,
    ) -> None:
        env_map = os.environ if env is None else env
        selected_backend = str(backend or env_map.get(BACKEND_ENV) or DEFAULT_BACKEND).strip().lower()
        if selected_backend in {"memory", "in-memory", "in_memory"}:
            selected_backend = "json"
            storage_path = "off"
        if selected_backend == "json":
            self.backend = selected_backend
            self._impl: AssistantConversationStoreBackend = JsonAssistantConversationStore(
                storage_path=storage_path,
            )
            return
        if selected_backend == "postgres":
            self.backend = selected_backend
            self._impl = PostgresAssistantConversationStore(
                dsn=str(
                    dsn
                    or env_map.get(DSN_ENV)
                    or env_map.get(DATABASE_URL_ENV)
                    or env_map.get("DATABASE_URL")
                    or ""
                ),
                schema=str(schema or env_map.get(SCHEMA_ENV) or DEFAULT_SCHEMA),
                surface=str(surface or env_map.get(SURFACE_ENV) or DEFAULT_SURFACE),
                owner_service=str(owner_service or env_map.get(OWNER_SERVICE_ENV) or DEFAULT_OWNER_SERVICE),
            )
            return
        raise ValueError(f"{BACKEND_ENV} must be json or postgres")

    def reset(self) -> None:
        return self._impl.reset()

    def create_session(self, **kwargs: Any) -> Dict[str, Any]:
        return self._impl.create_session(**kwargs)

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        return self._impl.get_session(session_id)

    def list_sessions(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self._impl.list_sessions(**kwargs)

    def touch_session(self, session_id: str, *, now: str) -> Optional[Dict[str, Any]]:
        return self._impl.touch_session(session_id, now=now)

    def append_turn(self, **kwargs: Any) -> Dict[str, Any]:
        return self._impl.append_turn(**kwargs)

    def list_turns(self, session_id: str) -> List[Dict[str, Any]]:
        return self._impl.list_turns(session_id)

    def list_all_turns(self) -> List[Dict[str, Any]]:
        return self._impl.list_all_turns()

    def put_idempotency(self, key: str, *, request_hash: str, result: Dict[str, Any]) -> Dict[str, Any]:
        return self._impl.put_idempotency(key, request_hash=request_hash, result=result)

    def get_idempotency(self, key: str) -> Optional[Dict[str, Any]]:
        return self._impl.get_idempotency(key)

    def put_assistant_session(self, **kwargs: Any) -> Dict[str, Any]:
        return self._impl.put_assistant_session(**kwargs)

    def get_assistant_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        return self._impl.get_assistant_session(session_id)


def build_assistant_conversation_store(
    *,
    storage_path: Optional[str] = None,
    env: Optional[Mapping[str, str]] = None,
) -> AssistantConversationStore:
    env_map = os.environ if env is None else env
    backend = str(env_map.get(BACKEND_ENV) or DEFAULT_BACKEND).strip().lower()
    posture = validate_persistence_posture(
        "operator-bff",
        env=env_map,
        backend_env_vars={BACKEND_ENV: DEFAULT_BACKEND},
        require_object_store=False,
    )
    if posture.errors:
        raise RuntimeError("; ".join(posture.errors))
    return AssistantConversationStore(backend=backend, storage_path=storage_path, env=env_map)
