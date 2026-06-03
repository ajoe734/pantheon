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


STORE_PATH_ENV = "PANTHEON_MANAGEMENT_AI_STORE_PATH"
ATTACHMENT_STORE_PATH_ENV = "PANTHEON_MANAGEMENT_AI_ATTACHMENT_STORE_PATH"
DEFAULT_STORE_PATH = "/tmp/pantheon-bff/management-ai-conversations.sqlite3"
DEFAULT_ATTACHMENT_STORE_PATH = "/tmp/pantheon-bff/management-ai-attachments"


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
    def __init__(self, *, storage_path: Optional[str] = None) -> None:
        self._storage_path = storage_path
        if self._storage_path is None:
            self._storage_path = os.getenv(ATTACHMENT_STORE_PATH_ENV, DEFAULT_ATTACHMENT_STORE_PATH)
        self._memory_objects: Dict[str, Tuple[bytes, str, str]] = {}

    def _enabled(self) -> bool:
        return not _storage_disabled(self._storage_path)

    def _path_for(self, attachment_id: str) -> Path:
        return Path(str(self._storage_path)) / f"{attachment_id}.bin"

    def store_inline_attachment(
        self,
        attachment: Dict[str, Any],
        *,
        session_id: str,
        turn_id: str,
    ) -> Dict[str, Any]:
        attachment_id = f"att_{uuid.uuid4().hex[:16]}"
        raw_data = str(attachment.get("dataBase64") or attachment.get("data_base64") or "")
        if "," in raw_data and raw_data.strip().lower().startswith("data:"):
            raw_data = raw_data.split(",", 1)[1]
        try:
            content = base64.b64decode(raw_data, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("attachment.dataBase64 must be valid base64") from exc

        mime_type = _clean_text(
            attachment.get("mimeType") or attachment.get("mime_type") or "application/octet-stream",
            max_len=160,
        )
        filename = _safe_filename(attachment.get("filename") or f"{attachment_id}.bin")
        if self._enabled():
            path = self._path_for(attachment_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
            tmp_path.write_bytes(content)
            os.replace(tmp_path, path)
        else:
            self._memory_objects[attachment_id] = (content, mime_type, filename)

        size_bytes = int(attachment.get("sizeBytes") or attachment.get("size_bytes") or len(content))
        storage_url = f"local://management-ai-attachments/{attachment_id}"
        return {
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
            "sessionId": session_id,
            "session_id": session_id,
            "turnId": turn_id,
            "turn_id": turn_id,
        }

    def read(self, attachment_id: str, metadata: Dict[str, Any]) -> Tuple[bytes, str, str]:
        clean_id = str(attachment_id or "").strip()
        if clean_id in self._memory_objects:
            return self._memory_objects[clean_id]
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
        self._sequence = 0
        if self._enabled():
            self._init_db()

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
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_management_ai_turns_session_created "
                    "ON management_ai_turns(session_id, created_at, sequence)"
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
            "createdAt": row["created_at"],
            "created_at": row["created_at"],
            "sequence": int(row["sequence"] or 0),
        }

    def reset(self) -> None:
        with self._lock:
            if self._enabled():
                with self._connect() as conn:
                    conn.execute("DELETE FROM management_ai_turns")
                    conn.execute("DELETE FROM management_ai_sessions")
            self._sessions = {}
            self._turns = {}
            self._sequence = 0

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        clean_session_id = str(session_id or "").strip()
        with self._lock:
            if self._enabled():
                with self._connect() as conn:
                    row = conn.execute(
                        "SELECT * FROM management_ai_sessions WHERE id = ?",
                        (clean_session_id,),
                    ).fetchone()
                return self._row_to_session(row) if row is not None else None
            session = self._sessions.get(clean_session_id)
            return dict(session) if session is not None else None

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
        with self._lock:
            if self._enabled():
                with self._connect() as conn:
                    existing = conn.execute(
                        "SELECT * FROM management_ai_sessions WHERE id = ?",
                        (clean_session_id,),
                    ).fetchone()
                    if existing is None:
                        conn.execute(
                            """
                            INSERT INTO management_ai_sessions
                              (id, owner_id, tenant_id, title, created_at, updated_at)
                            VALUES (?, ?, ?, ?, ?, ?)
                            """,
                            (clean_session_id, str(owner_id or ""), str(tenant_id or "") or None, clean_title, now, now),
                        )
                    else:
                        next_title = existing["title"] or clean_title
                        conn.execute(
                            """
                            UPDATE management_ai_sessions
                            SET updated_at = ?, title = ?
                            WHERE id = ?
                            """,
                            (now, next_title, clean_session_id),
                        )
                    row = conn.execute(
                        "SELECT * FROM management_ai_sessions WHERE id = ?",
                        (clean_session_id,),
                    ).fetchone()
                return self._row_to_session(row)

            existing = self._sessions.get(clean_session_id)
            if existing is None:
                existing = {
                    "id": clean_session_id,
                    "sessionId": clean_session_id,
                    "session_id": clean_session_id,
                    "ownerId": str(owner_id or ""),
                    "owner_id": str(owner_id or ""),
                    "tenantId": str(tenant_id or "") or None,
                    "tenant_id": str(tenant_id or "") or None,
                    "title": clean_title,
                    "createdAt": now,
                    "created_at": now,
                    "updatedAt": now,
                    "updated_at": now,
                }
                self._sessions[clean_session_id] = existing
                self._turns.setdefault(clean_session_id, [])
            else:
                existing["updatedAt"] = now
                existing["updated_at"] = now
                if clean_title and not existing.get("title"):
                    existing["title"] = clean_title
            return dict(existing)

    def store_attachments(
        self,
        raw_attachments: Any,
        *,
        session_id: str,
        turn_id: str,
    ) -> List[Dict[str, Any]]:
        if not isinstance(raw_attachments, list):
            return []
        normalized: List[Dict[str, Any]] = []
        for raw in raw_attachments[:12]:
            if not isinstance(raw, dict):
                continue
            if raw.get("dataBase64") or raw.get("data_base64"):
                normalized.append(
                    self._attachment_store.store_inline_attachment(
                        raw,
                        session_id=session_id,
                        turn_id=turn_id,
                    )
                )
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
        with self._lock:
            if self._enabled():
                with self._connect() as conn:
                    session = conn.execute(
                        "SELECT id FROM management_ai_sessions WHERE id = ?",
                        (clean_session_id,),
                    ).fetchone()
                    if session is None:
                        raise KeyError(clean_session_id)
                    next_sequence = int(
                        conn.execute(
                            "SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence FROM management_ai_turns"
                        ).fetchone()["next_sequence"]
                    )
                    conn.execute(
                        """
                        INSERT INTO management_ai_turns
                          (id, session_id, role, text, attachments, provider_status,
                           trace_id, ui_snapshot, ui_actions, created_at, sequence)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            clean_turn_id,
                            clean_session_id,
                            clean_role,
                            clean_text,
                            _json_dumps(clean_attachments),
                            _json_dumps(provider_status) if isinstance(provider_status, dict) else None,
                            clean_trace_id,
                            _json_dumps(ui_snapshot) if isinstance(ui_snapshot, dict) else None,
                            _json_dumps(clean_ui_actions),
                            created_at,
                            next_sequence,
                        ),
                    )
                    conn.execute(
                        "UPDATE management_ai_sessions SET updated_at = ? WHERE id = ?",
                        (created_at, clean_session_id),
                    )
                    row = conn.execute(
                        "SELECT * FROM management_ai_turns WHERE id = ?",
                        (clean_turn_id,),
                    ).fetchone()
                return self._row_to_turn(row)

            if clean_session_id not in self._sessions:
                raise KeyError(clean_session_id)
            self._sequence += 1
            provider = dict(provider_status) if isinstance(provider_status, dict) else None
            snapshot = dict(ui_snapshot) if isinstance(ui_snapshot, dict) else None
            turn = {
                "id": clean_turn_id,
                "turnId": clean_turn_id,
                "turn_id": clean_turn_id,
                "sessionId": clean_session_id,
                "session_id": clean_session_id,
                "role": clean_role,
                "text": clean_text,
                "content": clean_text,
                "attachments": clean_attachments,
                "providerStatus": provider,
                "provider_status": provider,
                "traceId": clean_trace_id,
                "trace_id": clean_trace_id,
                "uiSnapshot": snapshot,
                "ui_snapshot": snapshot,
                "uiActions": clean_ui_actions,
                "ui_actions": clean_ui_actions,
                "actions": clean_ui_actions,
                "createdAt": created_at,
                "created_at": created_at,
                "sequence": self._sequence,
            }
            self._turns.setdefault(clean_session_id, []).append(turn)
            session = self._sessions[clean_session_id]
            session["updatedAt"] = created_at
            session["updated_at"] = created_at
            return dict(turn)

    def list_turns(self, session_id: str) -> List[Dict[str, Any]]:
        clean_session_id = str(session_id or "").strip()
        with self._lock:
            if self._enabled():
                with self._connect() as conn:
                    rows = conn.execute(
                        """
                        SELECT * FROM management_ai_turns
                        WHERE session_id = ?
                        ORDER BY created_at ASC, sequence ASC
                        """,
                        (clean_session_id,),
                    ).fetchall()
                return [self._row_to_turn(row) for row in rows]
            turns = list(self._turns.get(clean_session_id) or [])
        return [
            dict(item)
            for item in sorted(
                turns,
                key=lambda item: (
                    str(item.get("createdAt") or item.get("created_at") or ""),
                    int(item.get("sequence") or 0),
                ),
            )
        ]

    def find_attachment(self, attachment_id: str) -> Optional[Tuple[Dict[str, Any], Dict[str, Any]]]:
        clean_id = str(attachment_id or "").strip()
        if self._enabled():
            with self._lock:
                with self._connect() as conn:
                    rows = conn.execute("SELECT * FROM management_ai_turns").fetchall()
                for row in rows:
                    turn = self._row_to_turn(row)
                    for attachment in turn.get("attachments") or []:
                        if not isinstance(attachment, dict):
                            continue
                        if str(attachment.get("id") or attachment.get("attachmentId") or attachment.get("attachment_id") or "") == clean_id:
                            return dict(attachment), turn
            return None

        with self._lock:
            for turns in self._turns.values():
                for turn in turns:
                    for attachment in turn.get("attachments") or []:
                        if not isinstance(attachment, dict):
                            continue
                        if str(attachment.get("id") or attachment.get("attachmentId") or attachment.get("attachment_id") or "") == clean_id:
                            return dict(attachment), dict(turn)
        return None

    def read_attachment(self, attachment_id: str, metadata: Dict[str, Any]) -> Tuple[bytes, str, str]:
        return self._attachment_store.read(attachment_id, metadata)
