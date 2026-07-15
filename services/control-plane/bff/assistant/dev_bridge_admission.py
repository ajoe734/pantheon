"""Durable non-terminal admission records for assistant dev-bridge packets.

The ordinary ``ai-task-archive/tasks/*.json`` namespace contains terminal task
snapshots.  A freshly dispatched dev-bridge task is deliberately *not*
terminal, so writing ``<task-id>.json`` there would make the status reconciler
prune an active task.  Bridge admission records therefore live in the nested
``assistant-dev-bridge-admissions`` namespace.  They remain under the task
archive root for supervisor/audit durability, while the terminal archive index
(which scans only flat ``tasks/*.json`` entries) keeps its existing semantics.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping


ADMISSION_SCHEMA = "pantheon.assistant-dev-bridge-admission.v1"
_ADMISSION_DIR = Path("ai-task-archive/tasks/assistant-dev-bridge-admissions")


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _ensure_directory(path: Path) -> None:
    missing: list[Path] = []
    cursor = path
    while not cursor.exists() and cursor.parent != cursor:
        missing.append(cursor)
        cursor = cursor.parent
    for directory in reversed(missing):
        directory.mkdir(exist_ok=True)
        _fsync_directory(directory.parent)


def _safe_packet_id(packet_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", str(packet_id or "").strip())
    return safe[:160] or "packet"


def admission_record_path(
    *, repo_root: str, packet_id: str, packet_digest: str
) -> Path:
    filename = f"{_safe_packet_id(packet_id)}--{packet_digest[:16]}.json"
    return Path(repo_root).resolve() / _ADMISSION_DIR / filename


def admission_display_path(path: Path, *, repo_root: str) -> str:
    try:
        return str(path.resolve().relative_to(Path(repo_root).resolve()))
    except ValueError:
        return str(path.resolve())


def _read_record(path: Path) -> Dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Bridge admission record is unreadable: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Bridge admission record is not an object: {path}")
    return payload


def load_admission_record(
    *, repo_root: str, packet_id: str, packet_digest: str
) -> Dict[str, Any] | None:
    path = admission_record_path(
        repo_root=repo_root,
        packet_id=packet_id,
        packet_digest=packet_digest,
    )
    record = _read_record(path)
    if record is None:
        return None
    if record.get("schema") != ADMISSION_SCHEMA:
        raise ValueError("Bridge admission record schema is unsupported")
    if record.get("packet_id") != packet_id or record.get("packet_digest") != packet_digest:
        raise ValueError("Bridge admission record does not match the signed packet")
    return record


def _atomic_write(path: Path, payload: Mapping[str, Any]) -> None:
    _ensure_directory(path.parent)
    serialized = json.dumps(
        payload,
        indent=2,
        ensure_ascii=False,
        sort_keys=True,
    ) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = -1
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
        _fsync_directory(path.parent)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary.exists():
            temporary.unlink()


def persist_admission_record(
    *,
    repo_root: str,
    packet_id: str,
    packet_digest: str,
    admitted_at: str,
    actor: Mapping[str, Any],
    mode: str,
    intent: str,
    conversation_id: str,
    source_turn_ids: Iterable[str],
    documents: Iterable[Mapping[str, Any]],
    audit_conversation_href: str | None,
    tasks: Iterable[Mapping[str, Any]],
    dispatch_records: Iterable[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Persist the packet admission commit point and return its canonical record.

    Re-entry after a crash returns the already durable record when the signed
    packet digest matches.  A digest/path mismatch fails closed.
    """

    path = admission_record_path(
        repo_root=repo_root,
        packet_id=packet_id,
        packet_digest=packet_digest,
    )
    existing = load_admission_record(
        repo_root=repo_root,
        packet_id=packet_id,
        packet_digest=packet_digest,
    )
    if existing is not None:
        return existing

    record: Dict[str, Any] = {
        "schema": ADMISSION_SCHEMA,
        "record_kind": "assistant_dev_bridge_admission",
        "packet_id": packet_id,
        "packet_digest": packet_digest,
        "admitted_at": admitted_at,
        "admission_record_path": admission_display_path(path, repo_root=repo_root),
        "actor": dict(actor),
        "mode": mode,
        "intent": intent,
        "conversation_id": conversation_id,
        "source_turn_ids": list(source_turn_ids),
        "documents": [dict(document) for document in documents],
        "audit_conversation_href": audit_conversation_href,
        "tasks": [dict(task) for task in tasks],
        "dispatch_records": [dict(item) for item in dispatch_records],
    }
    _atomic_write(path, record)
    persisted = load_admission_record(
        repo_root=repo_root,
        packet_id=packet_id,
        packet_digest=packet_digest,
    )
    if persisted is None:
        raise ValueError("Bridge admission record disappeared after persistence")
    return persisted
