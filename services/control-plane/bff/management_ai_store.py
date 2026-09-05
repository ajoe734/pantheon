from __future__ import annotations

import base64
import binascii
import json
import os
import re
import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import unquote

from .assistant_conversation_store import build_assistant_conversation_store, storage_disabled as _conversation_storage_disabled


STORE_PATH_ENV = "PANTHEON_MANAGEMENT_AI_STORE_PATH"
ATTACHMENT_STORE_PATH_ENV = "PANTHEON_MANAGEMENT_AI_ATTACHMENT_STORE_PATH"
ATTACHMENT_BUCKET_ENV = "PANTHEON_MGMT_AI_ATTACH_BUCKET"
ATTACHMENT_PREFIX_ENV = "PANTHEON_MGMT_AI_ATTACH_PREFIX"
ATTACHMENT_ALLOWED_MIME_TYPES_ENV = "PANTHEON_MGMT_AI_ATTACH_ALLOWED_MIME_TYPES"
ATTACHMENT_MAX_BYTES_ENV = "PANTHEON_MGMT_AI_ATTACH_MAX_BYTES"
ATTACHMENT_MAX_REQUEST_BYTES_ENV = "PANTHEON_MGMT_AI_ATTACH_MAX_REQUEST_BYTES"
ATTACHMENT_MAX_COUNT_ENV = "PANTHEON_MGMT_AI_ATTACH_MAX_COUNT"
DEFAULT_STORE_PATH = "/tmp/pantheon-bff/management-ai-conversations.json"
DEFAULT_ATTACHMENT_STORE_PATH = "/tmp/pantheon-bff/management-ai-attachments"
DEFAULT_ATTACHMENT_PREFIX = "management-ai-attachments"
DEFAULT_ATTACHMENT_ALLOWED_MIME_TYPES = ("image/png", "image/jpeg", "image/webp", "image/gif")
DEFAULT_ATTACHMENT_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_ATTACHMENT_MAX_REQUEST_BYTES = 20 * 1024 * 1024
DEFAULT_ATTACHMENT_MAX_COUNT = 12


class ManagementAiAttachmentError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int = 422,
        precondition_failed: str = "management_ai_attachment",
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.precondition_failed = precondition_failed
        self.details = dict(details or {})


class ManagementAiAttachmentTooLarge(ManagementAiAttachmentError):
    def __init__(
        self,
        message: str,
        *,
        precondition_failed: str = "management_ai_attachment_size",
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(
            message,
            status_code=413,
            precondition_failed=precondition_failed,
            details=details,
        )


def _storage_disabled(value: Optional[str]) -> bool:
    raw = str(value or "").strip()
    return raw.lower() in {"off", "false", "disabled", "none", ":memory:"}


def _clean_text(value: Any, *, max_len: int = 4000) -> str:
    clean = re.sub(r"\s+", " ", str(value or "").strip())
    if len(clean) > max_len:
        return f"{clean[:max_len]}..."
    return clean


def _safe_filename(value: Any) -> str:
    clean = os.path.basename(str(value or "attachment").strip()) or "attachment"
    return re.sub(r"[^A-Za-z0-9._-]+", "_", clean)[:180] or "attachment"


def _safe_object_part(value: Any, *, fallback: str, max_len: int = 120) -> str:
    clean = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip())
    return clean[:max_len] or fallback


def _safe_object_prefix(value: Any) -> str:
    clean = str(value or DEFAULT_ATTACHMENT_PREFIX).strip().strip("/")
    clean = re.sub(r"[^A-Za-z0-9._/-]+", "_", clean)
    clean = re.sub(r"/+", "/", clean).strip("/")
    return clean or DEFAULT_ATTACHMENT_PREFIX


def _positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _allowed_mime_types(value: Any) -> Tuple[str, ...]:
    raw = value if value not in (None, "") else ",".join(DEFAULT_ATTACHMENT_ALLOWED_MIME_TYPES)
    if isinstance(raw, str):
        parts = re.split(r"[\s,]+", raw)
    else:
        parts = list(raw or [])
    allowed = tuple(
        sorted({str(part or "").strip().lower() for part in parts if str(part or "").strip()})
    )
    return allowed or DEFAULT_ATTACHMENT_ALLOWED_MIME_TYPES


def _parse_gs_url(storage_url: str) -> Tuple[str, str]:
    clean = str(storage_url or "").strip()
    if not clean.startswith("gs://"):
        return "", ""
    without_scheme = clean[len("gs://") :]
    bucket, _, object_name = without_scheme.partition("/")
    return bucket.strip(), unquote(object_name.strip())


def _json_dumps(value: Any) -> str:
    return json.dumps(value if value is not None else None, ensure_ascii=False, sort_keys=True)


def _json_loads(value: Any, fallback: Any) -> Any:
    if value in (None, ""):
        return fallback
    try:
        loaded = json.loads(str(value))
    except json.JSONDecodeError:
        return fallback
    return loaded


class ManagementAiAttachmentStore:
    def __init__(
        self,
        *,
        storage_path: Optional[str] = None,
        bucket_name: Optional[str] = None,
        object_prefix: Optional[str] = None,
        allowed_mime_types: Optional[List[str]] = None,
        max_attachment_bytes: Optional[int] = None,
        max_request_bytes: Optional[int] = None,
        max_attachment_count: Optional[int] = None,
    ) -> None:
        self._storage_path = storage_path
        if self._storage_path is None:
            self._storage_path = os.getenv(ATTACHMENT_STORE_PATH_ENV, DEFAULT_ATTACHMENT_STORE_PATH)
        if bucket_name is None:
            bucket_name = os.getenv(ATTACHMENT_BUCKET_ENV, "")
        self._bucket_name = str(bucket_name or "").strip()
        self._object_prefix = _safe_object_prefix(object_prefix or os.getenv(ATTACHMENT_PREFIX_ENV, ""))
        self._allowed_mime_types = _allowed_mime_types(
            allowed_mime_types
            if allowed_mime_types is not None
            else os.getenv(ATTACHMENT_ALLOWED_MIME_TYPES_ENV, "")
        )
        self.max_attachment_bytes = _positive_int(
            max_attachment_bytes
            if max_attachment_bytes is not None
            else os.getenv(ATTACHMENT_MAX_BYTES_ENV),
            DEFAULT_ATTACHMENT_MAX_BYTES,
        )
        self.max_request_bytes = _positive_int(
            max_request_bytes
            if max_request_bytes is not None
            else os.getenv(ATTACHMENT_MAX_REQUEST_BYTES_ENV),
            DEFAULT_ATTACHMENT_MAX_REQUEST_BYTES,
        )
        self.max_attachment_count = _positive_int(
            max_attachment_count
            if max_attachment_count is not None
            else os.getenv(ATTACHMENT_MAX_COUNT_ENV),
            DEFAULT_ATTACHMENT_MAX_COUNT,
        )
        self._memory_objects: Dict[str, Tuple[bytes, str, str]] = {}

    def _enabled(self) -> bool:
        return not _conversation_storage_disabled(self._storage_path)

    def _gcs_enabled(self) -> bool:
        return bool(self._bucket_name)

    def _gcs_bucket(self, bucket_name: Optional[str] = None) -> Any:
        try:
            from google.cloud import storage  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "google-cloud-storage is required when PANTHEON_MGMT_AI_ATTACH_BUCKET is set"
            ) from exc
        return storage.Client().bucket(str(bucket_name or self._bucket_name))

    def _path_for(self, attachment_id: str) -> Path:
        return Path(str(self._storage_path)) / f"{attachment_id}.bin"

    def _object_name(self, *, attachment_id: str, session_id: str, turn_id: str, filename: str) -> str:
        session_part = _safe_object_part(session_id, fallback="session", max_len=100)
        turn_part = _safe_object_part(turn_id, fallback="turn", max_len=100)
        file_part = _safe_filename(filename)
        return f"{self._object_prefix}/{session_part}/{turn_part}/{attachment_id}-{file_part}"

    def _storage_url(self, *, attachment_id: str, object_name: str) -> str:
        if self._gcs_enabled():
            return f"gs://{self._bucket_name}/{object_name}"
        return f"local://management-ai-attachments/{attachment_id}"

    def prepare_inline_attachment(
        self,
        attachment: Dict[str, Any],
        *,
        session_id: str,
        turn_id: str,
    ) -> Tuple[Dict[str, Any], bytes]:
        attachment_id = f"att_{uuid.uuid4().hex[:16]}"
        raw_data = str(attachment.get("dataBase64") or attachment.get("data_base64") or "")
        if "," in raw_data and raw_data.strip().lower().startswith("data:"):
            raw_data = raw_data.split(",", 1)[1]
        if not raw_data:
            raise ManagementAiAttachmentError(
                "attachment.dataBase64 is required for inline Management AI attachments",
                precondition_failed="management_ai_attachment_data",
            )
        try:
            content = base64.b64decode(raw_data, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ManagementAiAttachmentError(
                "attachment.dataBase64 must be valid base64",
                precondition_failed="management_ai_attachment_data",
            ) from exc

        mime_type = _clean_text(
            attachment.get("mimeType") or attachment.get("mime_type") or "application/octet-stream",
            max_len=160,
        ).lower()
        if mime_type not in self._allowed_mime_types:
            raise ManagementAiAttachmentError(
                f"attachment.mimeType {mime_type!r} is not allowed",
                precondition_failed="management_ai_attachment_mime_type",
                details={"allowedMimeTypes": list(self._allowed_mime_types), "actualMimeType": mime_type},
            )

        size_bytes = len(content)
        if size_bytes > self.max_attachment_bytes:
            raise ManagementAiAttachmentTooLarge(
                f"attachment must be at most {self.max_attachment_bytes} bytes",
                details={"maxAttachmentBytes": self.max_attachment_bytes, "actualAttachmentBytes": size_bytes},
            )

        filename = _safe_filename(attachment.get("filename") or f"{attachment_id}.bin")
        object_name = self._object_name(
            attachment_id=attachment_id,
            session_id=session_id,
            turn_id=turn_id,
            filename=filename,
        )
        storage_url = self._storage_url(attachment_id=attachment_id, object_name=object_name)
        metadata = {
            "id": attachment_id,
            "attachmentId": attachment_id,
            "attachment_id": attachment_id,
            "kind": _clean_text(attachment.get("kind") or "file", max_len=80),
            "mimeType": mime_type,
            "mime_type": mime_type,
            "filename": filename,
            "sizeBytes": size_bytes,
            "size_bytes": size_bytes,
            "storageUrl": storage_url,
            "storage_url": storage_url,
            "objectName": object_name,
            "object_name": object_name,
            "sessionId": session_id,
            "session_id": session_id,
            "turnId": turn_id,
            "turn_id": turn_id,
        }
        return metadata, content

    def _write_prepared_attachment(self, metadata: Dict[str, Any], content: bytes) -> None:
        attachment_id = str(metadata.get("id") or metadata.get("attachmentId") or metadata.get("attachment_id") or "")
        mime_type = str(metadata.get("mimeType") or metadata.get("mime_type") or "application/octet-stream")
        filename = str(metadata.get("filename") or f"{attachment_id}.bin")
        if self._gcs_enabled():
            object_name = str(metadata.get("objectName") or metadata.get("object_name") or "").strip()
            blob = self._gcs_bucket().blob(object_name)
            blob.upload_from_string(content, content_type=mime_type)
            return
        if self._enabled():
            path = self._path_for(attachment_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
            tmp_path.write_bytes(content)
            os.replace(tmp_path, path)
            return
        self._memory_objects[attachment_id] = (content, mime_type, filename)

    def store_inline_attachment(
        self,
        attachment: Dict[str, Any],
        *,
        session_id: str,
        turn_id: str,
    ) -> Dict[str, Any]:
        metadata, content = self.prepare_inline_attachment(
            attachment,
            session_id=session_id,
            turn_id=turn_id,
        )
        self._write_prepared_attachment(metadata, content)
        return metadata

    def read(self, attachment_id: str, metadata: Dict[str, Any]) -> Tuple[bytes, str, str]:
        clean_id = str(attachment_id or "").strip()
        if clean_id in self._memory_objects:
            return self._memory_objects[clean_id]
        storage_url = str(metadata.get("storageUrl") or metadata.get("storage_url") or "")
        bucket_name, object_name = _parse_gs_url(storage_url)
        if bucket_name and object_name:
            blob = self._gcs_bucket(bucket_name).blob(object_name)
            content = blob.download_as_bytes()
            mime_type = str(metadata.get("mimeType") or metadata.get("mime_type") or "application/octet-stream")
            filename = str(metadata.get("filename") or f"{clean_id}.bin")
            return content, mime_type, filename
        path = self._path_for(clean_id)
        content = path.read_bytes()
        mime_type = str(metadata.get("mimeType") or metadata.get("mime_type") or "application/octet-stream")
        filename = str(metadata.get("filename") or f"{clean_id}.bin")
        return content, mime_type, filename


class ManagementAiConversationStore:
    def __init__(
        self,
        *,
        storage_path: Optional[str] = None,
        attachment_store: Optional[ManagementAiAttachmentStore] = None,
    ) -> None:
        self._lock = threading.Lock()
        self._storage_path = storage_path
        if self._storage_path is None:
            self._storage_path = os.getenv(STORE_PATH_ENV, DEFAULT_STORE_PATH)
        self._attachment_store = attachment_store or ManagementAiAttachmentStore()
        self._sessions: Dict[str, Dict[str, Any]] = {}
        self._turns: Dict[str, List[Dict[str, Any]]] = {}
        self._assistant_sessions: Dict[str, Dict[str, Any]] = {}
        self._sequence = 0
        self._conversation_store = build_assistant_conversation_store(
            storage_path=None if self._storage_path is None else str(self._storage_path),
        )

    def _enabled(self) -> bool:
        return not _storage_disabled(self._storage_path)

    def _connect(self) -> sqlite3.Connection:
        path = Path(str(self._storage_path))
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS management_ai_sessions (
                      id TEXT PRIMARY KEY,
                      owner_id TEXT NOT NULL,
                      tenant_id TEXT,
                      title TEXT,
                      created_at TEXT NOT NULL,
                      updated_at TEXT NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS management_ai_turns (
                      id TEXT PRIMARY KEY,
                      session_id TEXT NOT NULL REFERENCES management_ai_sessions(id) ON DELETE CASCADE,
                      role TEXT NOT NULL CHECK (role IN ('user','assistant','system')),
                      text TEXT NOT NULL DEFAULT '',
                      attachments TEXT NOT NULL DEFAULT '[]',
                      provider_status TEXT,
                      trace_id TEXT,
                      ui_snapshot TEXT,
                      ui_actions TEXT NOT NULL DEFAULT '[]',
                      created_at TEXT NOT NULL,
                      sequence INTEGER NOT NULL
                    )
                    """
                )
                turn_columns = {
                    str(row["name"])
                    for row in conn.execute("PRAGMA table_info(management_ai_turns)").fetchall()
                }
                if "assistant_metadata" not in turn_columns:
                    conn.execute("ALTER TABLE management_ai_turns ADD COLUMN assistant_metadata TEXT")
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_management_ai_turns_session_created "
                    "ON management_ai_turns(session_id, created_at, sequence)"
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS management_ai_assistant_sessions (
                      session_id TEXT PRIMARY KEY REFERENCES management_ai_sessions(id) ON DELETE CASCADE,
                      mode TEXT NOT NULL,
                      actor_id TEXT NOT NULL,
                      roles TEXT NOT NULL DEFAULT '[]',
                      capabilities TEXT NOT NULL DEFAULT '[]',
                      created_at TEXT NOT NULL,
                      expires_at TEXT,
                      status TEXT NOT NULL,
                      reason TEXT,
                      ttl_seconds INTEGER,
                      context_pack_id TEXT,
                      provider_run_id TEXT,
                      audit_refs TEXT NOT NULL DEFAULT '[]',
                      revoked_at TEXT,
                      revoke_reason TEXT,
                      updated_at TEXT NOT NULL
                    )
                    """
                )

    def _row_to_session(self, row: sqlite3.Row) -> Dict[str, Any]:
        session_id = str(row["id"])
        return {
            "id": session_id,
            "sessionId": session_id,
            "session_id": session_id,
            "ownerId": row["owner_id"],
            "owner_id": row["owner_id"],
            "tenantId": row["tenant_id"],
            "tenant_id": row["tenant_id"],
            "title": row["title"] or "",
            "createdAt": row["created_at"],
            "created_at": row["created_at"],
            "updatedAt": row["updated_at"],
            "updated_at": row["updated_at"],
        }

    def _row_to_turn(self, row: sqlite3.Row) -> Dict[str, Any]:
        turn_id = str(row["id"])
        session_id = str(row["session_id"])
        provider_status = _json_loads(row["provider_status"], None)
        ui_snapshot = _json_loads(row["ui_snapshot"], None)
        ui_actions = _json_loads(row["ui_actions"], [])
        attachments = _json_loads(row["attachments"], [])
        assistant_metadata = (
            _json_loads(row["assistant_metadata"], {})
            if "assistant_metadata" in row.keys()
            else {}
        )
        return {
            "id": turn_id,
            "turnId": turn_id,
            "turn_id": turn_id,
            "sessionId": session_id,
            "session_id": session_id,
            "role": row["role"],
            "text": row["text"] or "",
            "content": row["text"] or "",
            "attachments": attachments if isinstance(attachments, list) else [],
            "providerStatus": provider_status if isinstance(provider_status, dict) else None,
            "provider_status": provider_status if isinstance(provider_status, dict) else None,
            "traceId": row["trace_id"],
            "trace_id": row["trace_id"],
            "uiSnapshot": ui_snapshot if isinstance(ui_snapshot, dict) else None,
            "ui_snapshot": ui_snapshot if isinstance(ui_snapshot, dict) else None,
            "uiActions": ui_actions if isinstance(ui_actions, list) else [],
            "ui_actions": ui_actions if isinstance(ui_actions, list) else [],
            "actions": ui_actions if isinstance(ui_actions, list) else [],
            "assistantMetadata": assistant_metadata if isinstance(assistant_metadata, dict) else {},
            "assistant_metadata": assistant_metadata if isinstance(assistant_metadata, dict) else {},
            "createdAt": row["created_at"],
            "created_at": row["created_at"],
            "sequence": int(row["sequence"] or 0),
        }

    def _row_to_assistant_session(self, row: sqlite3.Row) -> Dict[str, Any]:
        roles = _json_loads(row["roles"], [])
        capabilities = _json_loads(row["capabilities"], [])
        audit_refs = _json_loads(row["audit_refs"], [])
        ttl_seconds = row["ttl_seconds"]
        return {
            "sessionId": row["session_id"],
            "session_id": row["session_id"],
            "mode": row["mode"],
            "actorId": row["actor_id"],
            "actor_id": row["actor_id"],
            "roles": roles if isinstance(roles, list) else [],
            "capabilities": capabilities if isinstance(capabilities, list) else [],
            "createdAt": row["created_at"],
            "created_at": row["created_at"],
            "expiresAt": row["expires_at"],
            "expires_at": row["expires_at"],
            "status": row["status"],
            "reason": row["reason"],
            "ttlSeconds": int(ttl_seconds) if ttl_seconds is not None else None,
            "ttl_seconds": int(ttl_seconds) if ttl_seconds is not None else None,
            "contextPackId": row["context_pack_id"],
            "context_pack_id": row["context_pack_id"],
            "providerRunId": row["provider_run_id"],
            "provider_run_id": row["provider_run_id"],
            "auditRefs": audit_refs if isinstance(audit_refs, list) else [],
            "audit_refs": audit_refs if isinstance(audit_refs, list) else [],
            "revokedAt": row["revoked_at"],
            "revoked_at": row["revoked_at"],
            "revokeReason": row["revoke_reason"],
            "revoke_reason": row["revoke_reason"],
            "updatedAt": row["updated_at"],
            "updated_at": row["updated_at"],
        }

    def reset(self) -> None:
        self._conversation_store.reset()
        with self._lock:
            self._sessions = {}
            self._turns = {}
            self._assistant_sessions = {}
            self._sequence = 0

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        clean_session_id = str(session_id or "").strip()
        return self._conversation_store.get_session(clean_session_id)

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
        return self._conversation_store.list_sessions(
            owner_id=clean_owner_id or None,
            tenant_id=clean_tenant_id or None,
            limit=clean_limit,
        )

    def upsert_session(
        self,
        *,
        session_id: str,
        owner_id: str,
        tenant_id: Optional[str],
        now: str,
        title: Optional[str] = None,
    ) -> Dict[str, Any]:
        clean_session_id = str(session_id or "").strip()
        clean_title = _clean_text(title or "", max_len=160)
        return self._conversation_store.create_session(
            session_id=clean_session_id,
            owner_id=owner_id,
            tenant_id=tenant_id,
            now=now,
            title=clean_title,
        )

    def store_attachments(
        self,
        raw_attachments: Any,
        *,
        session_id: str,
        turn_id: str,
    ) -> List[Dict[str, Any]]:
        if not isinstance(raw_attachments, list):
            return []
        if len(raw_attachments) > self._attachment_store.max_attachment_count:
            raise ManagementAiAttachmentTooLarge(
                f"attachments must include at most {self._attachment_store.max_attachment_count} items",
                precondition_failed="management_ai_attachment_count",
                details={
                    "maxAttachmentCount": self._attachment_store.max_attachment_count,
                    "actualAttachmentCount": len(raw_attachments),
                },
            )
        normalized: List[Dict[str, Any]] = []
        pending_inline: List[Tuple[Dict[str, Any], bytes]] = []
        total_inline_bytes = 0
        for raw in raw_attachments:
            if not isinstance(raw, dict):
                continue
            if raw.get("dataBase64") or raw.get("data_base64"):
                metadata, content = self._attachment_store.prepare_inline_attachment(
                    raw,
                    session_id=session_id,
                    turn_id=turn_id,
                )
                total_inline_bytes += int(metadata.get("sizeBytes") or metadata.get("size_bytes") or len(content))
                if total_inline_bytes > self._attachment_store.max_request_bytes:
                    raise ManagementAiAttachmentTooLarge(
                        f"request attachments must total at most {self._attachment_store.max_request_bytes} bytes",
                        precondition_failed="management_ai_attachment_total_size",
                        details={
                            "maxRequestAttachmentBytes": self._attachment_store.max_request_bytes,
                            "actualRequestAttachmentBytes": total_inline_bytes,
                        },
                    )
                pending_inline.append((metadata, content))
                normalized.append(metadata)
                continue
            storage_url = raw.get("storageUrl") or raw.get("storage_url") or raw.get("url")
            if storage_url:
                attachment_id = str(raw.get("id") or raw.get("attachmentId") or raw.get("attachment_id") or f"att_{uuid.uuid4().hex[:16]}")
                mime_type = _clean_text(raw.get("mimeType") or raw.get("mime_type") or "application/octet-stream", max_len=160)
                size_bytes = int(raw.get("sizeBytes") or raw.get("size_bytes") or 0)
                normalized.append(
                    {
                        "id": attachment_id,
                        "attachmentId": attachment_id,
                        "attachment_id": attachment_id,
                        "kind": _clean_text(raw.get("kind") or "file", max_len=80),
                        "mimeType": mime_type,
                        "mime_type": mime_type,
                        "filename": _safe_filename(raw.get("filename") or attachment_id),
                        "sizeBytes": size_bytes,
                        "size_bytes": size_bytes,
                        "storageUrl": str(storage_url),
                        "storage_url": str(storage_url),
                        "sessionId": session_id,
                        "session_id": session_id,
                        "turnId": turn_id,
                        "turn_id": turn_id,
                    }
                )
        for metadata, content in pending_inline:
            self._attachment_store._write_prepared_attachment(metadata, content)
        return normalized

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
        clean_role = str(role or "").strip().lower()
        if clean_role not in {"user", "assistant", "system"}:
            raise ValueError("management AI turn role must be user, assistant, or system")
        clean_turn_id = str(turn_id or "").strip()
        clean_text = str(text or "")
        clean_trace_id = str(trace_id or "").strip() or None
        clean_attachments = list(attachments or [])
        clean_ui_actions = list(ui_actions or [])
        clean_assistant_metadata = dict(assistant_metadata) if isinstance(assistant_metadata, dict) else {}
        return self._conversation_store.append_turn(
            turn_id=clean_turn_id,
            session_id=clean_session_id,
            role=clean_role,
            text=clean_text,
            created_at=created_at,
            trace_id=clean_trace_id,
            attachments=clean_attachments,
            provider_status=provider_status,
            ui_snapshot=ui_snapshot,
            ui_actions=clean_ui_actions,
            assistant_metadata=clean_assistant_metadata,
        )

    def list_turns(self, session_id: str) -> List[Dict[str, Any]]:
        clean_session_id = str(session_id or "").strip()
        return self._conversation_store.list_turns(clean_session_id)

    def upsert_assistant_session(
        self,
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
        updated_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        clean_session_id = str(session_id or "").strip()
        payload = self._conversation_store.put_assistant_session(
            session_id=clean_session_id,
            mode=mode,
            actor_id=actor_id,
            roles=roles,
            capabilities=capabilities,
            created_at=created_at,
            expires_at=expires_at,
            status=status,
            reason=reason,
            ttl_seconds=ttl_seconds,
            context_pack_id=context_pack_id,
            provider_run_id=provider_run_id,
            audit_refs=audit_refs,
            revoked_at=revoked_at,
            revoke_reason=revoke_reason,
            updated_at=updated_at,
        )
        return payload

    def get_assistant_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        clean_session_id = str(session_id or "").strip()
        return self._conversation_store.get_assistant_session(clean_session_id)

    def find_attachment(self, attachment_id: str) -> Optional[Tuple[Dict[str, Any], Dict[str, Any]]]:
        clean_id = str(attachment_id or "").strip()
        for turn in self._conversation_store.list_all_turns():
            for attachment in turn.get("attachments") or []:
                if not isinstance(attachment, dict):
                    continue
                if str(attachment.get("id") or attachment.get("attachmentId") or attachment.get("attachment_id") or "") == clean_id:
                    return dict(attachment), dict(turn)
        return None

    def read_attachment(self, attachment_id: str, metadata: Dict[str, Any]) -> Tuple[bytes, str, str]:
        return self._attachment_store.read(attachment_id, metadata)

    def put_idempotency(self, key: str, *, request_hash: str, result: Dict[str, Any]) -> Dict[str, Any]:
        return self._conversation_store.put_idempotency(key, request_hash=request_hash, result=result)

    def get_idempotency(self, key: str) -> Optional[Dict[str, Any]]:
        return self._conversation_store.get_idempotency(key)
