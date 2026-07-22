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

import errno
import hashlib
import json
import os
import re
import secrets
import stat
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping


ADMISSION_SCHEMA = "pantheon.assistant-dev-bridge-admission.v1"
_ADMISSION_DIR = Path("ai-task-archive/tasks/assistant-dev-bridge-admissions")
_PROVENANCE_FIELDS = (
    "packet_version",
    "actor",
    "mode",
    "intent",
    "conversation_id",
    "source_turn_ids",
    "documents",
    "audit_conversation_href",
    "emitted_at",
    "constraints",
    "tasks",
)


def _repo_root(repo_root: str) -> Path:
    try:
        root = Path(repo_root).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"Bridge admission repo root is invalid: {repo_root!r}") from exc
    if not root.is_dir():
        raise ValueError(f"Bridge admission repo root is not a directory: {root}")
    return root


def _open_admission_directory(
    *, repo_root: str, create: bool
) -> tuple[Path, int]:
    """Open the admission directory without following any child symlink.

    Walking from the canonical repo root with ``openat`` + ``O_NOFOLLOW``
    keeps every component pinned to the checked directory.  A pre-existing or
    racing symlink therefore fails closed instead of redirecting an admission
    record outside the repository.
    """

    root = _repo_root(repo_root)
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    current_fd = os.open(root, flags)
    current_path = root
    try:
        for component in _ADMISSION_DIR.parts:
            try:
                next_fd = os.open(component, flags, dir_fd=current_fd)
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    os.mkdir(component, mode=0o700, dir_fd=current_fd)
                    os.fsync(current_fd)
                except FileExistsError:
                    # A concurrent creator won the race; the no-follow open
                    # below still validates what appeared.
                    pass
                try:
                    next_fd = os.open(component, flags, dir_fd=current_fd)
                except OSError as exc:
                    raise ValueError(
                        f"Bridge admission directory component is unsafe: {current_path / component}"
                    ) from exc
            except OSError as exc:
                if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                    raise ValueError(
                        f"Bridge admission directory component is a symlink or non-directory: "
                        f"{current_path / component}"
                    ) from exc
                raise
            if not stat.S_ISDIR(os.fstat(next_fd).st_mode):
                os.close(next_fd)
                raise ValueError(
                    f"Bridge admission directory component is not a directory: "
                    f"{current_path / component}"
                )
            os.close(current_fd)
            current_fd = next_fd
            current_path /= component
        return current_path, current_fd
    except Exception:
        os.close(current_fd)
        raise


def _safe_packet_id(packet_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", str(packet_id or "").strip())
    return safe[:160] or "packet"


def _validated_packet_digest(packet_digest: str) -> str:
    digest = str(packet_digest or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("Bridge admission packet digest must be a SHA-256 hex digest")
    return digest


def admission_record_path(
    *, repo_root: str, packet_id: str, packet_digest: str
) -> Path:
    digest = _validated_packet_digest(packet_digest)
    root = _repo_root(repo_root)
    try:
        directory, descriptor = _open_admission_directory(
            repo_root=str(root), create=False
        )
    except FileNotFoundError:
        directory = root / _ADMISSION_DIR
    else:
        os.close(descriptor)
    filename = f"{_safe_packet_id(packet_id)}--{digest[:16]}.json"
    return directory / filename


def admission_display_path(path: Path, *, repo_root: str) -> str:
    try:
        return str(path.relative_to(_repo_root(repo_root)))
    except ValueError:
        return str(path)


def _read_record(path: Path, *, repo_root: str) -> Dict[str, Any] | None:
    try:
        directory, directory_fd = _open_admission_directory(
            repo_root=repo_root, create=False
        )
    except FileNotFoundError:
        return None
    if path.parent != directory:
        os.close(directory_fd)
        raise ValueError("Bridge admission record path escaped its canonical directory")
    descriptor = -1
    try:
        try:
            descriptor = os.open(
                path.name,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=directory_fd,
            )
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise ValueError(f"Bridge admission record is unsafe: {path}") from exc
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError(f"Bridge admission record is not a regular file: {path}")
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            descriptor = -1
            payload = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Bridge admission record is unreadable: {path}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(directory_fd)
    if not isinstance(payload, dict):
        raise ValueError(f"Bridge admission record is not an object: {path}")
    return payload


def _canonical_sha256(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def admission_record_payload_digest(record: Mapping[str, Any]) -> str:
    payload = {
        key: value
        for key, value in record.items()
        if key != "record_payload_sha256"
    }
    return _canonical_sha256(payload)


def _validate_tasks_and_dispatch(record: Mapping[str, Any]) -> None:
    tasks = record.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("Bridge admission record tasks must be a non-empty list")
    task_ids: list[str] = []
    owners: dict[str, tuple[str, str]] = {}
    for item in tasks:
        if not isinstance(item, Mapping):
            raise ValueError("Bridge admission task provenance must be an object")
        task_id = str(item.get("task_id") or "").strip()
        task_spec = item.get("task_spec")
        spec_hash = str(item.get("task_spec_hash") or "").strip()
        if not task_id or not isinstance(task_spec, Mapping):
            raise ValueError("Bridge admission task provenance is incomplete")
        if spec_hash != _canonical_sha256(task_spec):
            raise ValueError("Bridge admission task spec hash does not match its task spec")
        if str(task_spec.get("id") or "").strip() != task_id:
            raise ValueError("Bridge admission task id does not match its task spec")
        task_ids.append(task_id)
        owners[task_id] = (
            str(task_spec.get("owner") or ""),
            str(task_spec.get("reviewer") or ""),
        )
    if len(set(task_ids)) != len(task_ids):
        raise ValueError("Bridge admission record contains duplicate task ids")

    dispatch_records = record.get("dispatch_records")
    if not isinstance(dispatch_records, list) or len(dispatch_records) != len(task_ids):
        raise ValueError("Bridge admission dispatch records do not match its tasks")
    dispatched_ids: list[str] = []
    for item in dispatch_records:
        if not isinstance(item, Mapping):
            raise ValueError("Bridge admission dispatch record must be an object")
        task_id = str(item.get("taskId") or item.get("task_id") or "").strip()
        if task_id not in owners:
            raise ValueError("Bridge admission dispatch record references an unknown task")
        owner, reviewer = owners[task_id]
        if str(item.get("owner") or "") != owner or str(item.get("reviewer") or "") != reviewer:
            raise ValueError("Bridge admission dispatch identity does not match the task spec")
        if str(item.get("status") or "") != "dispatched" or item.get("error") not in (None, ""):
            raise ValueError("Bridge admission contains a non-successful dispatch record")
        dispatched_ids.append(task_id)
    if dispatched_ids != task_ids:
        raise ValueError("Bridge admission dispatch record order does not match its tasks")


def _validate_record(
    record: Mapping[str, Any],
    *,
    path: Path,
    repo_root: str,
    packet_id: str,
    packet_digest: str,
    expected_provenance: Mapping[str, Any] | None,
) -> None:
    if record.get("schema") != ADMISSION_SCHEMA:
        raise ValueError("Bridge admission record schema is unsupported")
    if record.get("record_kind") != "assistant_dev_bridge_admission":
        raise ValueError("Bridge admission record kind is unsupported")
    if record.get("durable") is not True:
        raise ValueError("Bridge admission record is not marked durable")
    if record.get("packet_id") != packet_id or record.get("packet_digest") != packet_digest:
        raise ValueError("Bridge admission record does not match the signed packet")
    if record.get("admission_record_path") != admission_display_path(path, repo_root=repo_root):
        raise ValueError("Bridge admission record path does not match its canonical path")
    if not str(record.get("admitted_at") or "").strip():
        raise ValueError("Bridge admission record admitted_at is missing")
    payload_digest = str(record.get("record_payload_sha256") or "").strip()
    if not re.fullmatch(r"[0-9a-f]{64}", payload_digest):
        raise ValueError("Bridge admission record payload digest is missing")
    if payload_digest != admission_record_payload_digest(record):
        raise ValueError("Bridge admission record payload digest mismatch")
    _validate_tasks_and_dispatch(record)
    if expected_provenance is not None:
        for field in _PROVENANCE_FIELDS:
            if record.get(field) != expected_provenance.get(field):
                raise ValueError(
                    f"Bridge admission record signed provenance mismatch: {field}"
                )


def load_admission_record(
    *,
    repo_root: str,
    packet_id: str,
    packet_digest: str,
    expected_provenance: Mapping[str, Any] | None = None,
) -> Dict[str, Any] | None:
    digest = _validated_packet_digest(packet_digest)
    path = admission_record_path(
        repo_root=repo_root,
        packet_id=packet_id,
        packet_digest=digest,
    )
    record = _read_record(path, repo_root=repo_root)
    if record is None:
        return None
    _validate_record(
        record,
        path=path,
        repo_root=repo_root,
        packet_id=packet_id,
        packet_digest=digest,
        expected_provenance=expected_provenance,
    )
    return record


def _atomic_write(path: Path, payload: Mapping[str, Any], *, repo_root: str) -> None:
    directory, directory_fd = _open_admission_directory(repo_root=repo_root, create=True)
    if path.parent != directory:
        os.close(directory_fd)
        raise ValueError("Bridge admission record path escaped its canonical directory")
    serialized = json.dumps(
        payload,
        indent=2,
        ensure_ascii=False,
        sort_keys=True,
    ) + "\n"
    descriptor = -1
    temporary_name = ""
    try:
        for _ in range(16):
            candidate = f".{path.name}.{secrets.token_hex(8)}.tmp"
            try:
                descriptor = os.open(
                    candidate,
                    os.O_WRONLY
                    | os.O_CREAT
                    | os.O_EXCL
                    | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                    dir_fd=directory_fd,
                )
            except FileExistsError:
                continue
            temporary_name = candidate
            break
        if descriptor < 0:
            raise OSError("Could not allocate a bridge admission temporary file")
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = -1
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(
            temporary_name,
            path.name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
        temporary_name = ""
        os.fsync(directory_fd)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary_name:
            try:
                os.unlink(temporary_name, dir_fd=directory_fd)
            except FileNotFoundError:
                pass
        os.close(directory_fd)


def persist_admission_record(
    *,
    repo_root: str,
    packet_id: str,
    packet_digest: str,
    admitted_at: str,
    packet_version: str,
    actor: Mapping[str, Any],
    mode: str,
    intent: str,
    conversation_id: str,
    source_turn_ids: Iterable[str],
    documents: Iterable[Mapping[str, Any]],
    audit_conversation_href: str | None,
    emitted_at: str,
    constraints: Mapping[str, Any],
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
    provenance: Dict[str, Any] = {
        "packet_version": packet_version,
        "actor": dict(actor),
        "mode": mode,
        "intent": intent,
        "conversation_id": conversation_id,
        "source_turn_ids": list(source_turn_ids),
        "documents": [dict(document) for document in documents],
        "audit_conversation_href": audit_conversation_href,
        "emitted_at": emitted_at,
        "constraints": dict(constraints),
        "tasks": [dict(task) for task in tasks],
    }
    existing = load_admission_record(
        repo_root=repo_root,
        packet_id=packet_id,
        packet_digest=packet_digest,
        expected_provenance=provenance,
    )
    if existing is not None:
        return existing

    record: Dict[str, Any] = {
        "schema": ADMISSION_SCHEMA,
        "record_kind": "assistant_dev_bridge_admission",
        "durable": True,
        "packet_id": packet_id,
        "packet_digest": packet_digest,
        "admitted_at": admitted_at,
        "admission_record_path": admission_display_path(path, repo_root=repo_root),
        **provenance,
        "dispatch_records": [dict(item) for item in dispatch_records],
    }
    record["record_payload_sha256"] = admission_record_payload_digest(record)
    _atomic_write(path, record, repo_root=repo_root)
    persisted = load_admission_record(
        repo_root=repo_root,
        packet_id=packet_id,
        packet_digest=packet_digest,
        expected_provenance=provenance,
    )
    if persisted is None:
        raise ValueError("Bridge admission record disappeared after persistence")
    return persisted
