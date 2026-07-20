"""Durable append-only task-state journal for Supervisor Rewrite Phase 6.

The first cutover stage is intentionally shadow-only: ``ai-status.json`` remains
the incumbent write target while every committed state is also appended here.
Each event carries the complete state, a state digest, and a previous-event hash
so chain corruption and projection divergence are detectable before the journal
becomes authoritative.
"""
from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import os
import stat
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


EVENT_VERSION = 1
EVENT_TYPE_STATE_COMMITTED = "task_state_committed"


class TaskStateStoreError(RuntimeError):
    """The task-state journal is unsafe, corrupt, or fails replay validation."""


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _event_digest_payload(event: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in event.items()
        if key not in {"event_id", "event_sha256"}
    }


def _first_symlink_component(path: Path) -> Path | None:
    absolute = path.expanduser().absolute()
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        try:
            if current.is_symlink():
                return current
            if not current.exists():
                return None
        except OSError:
            return current
    return None


def _prepare_parent(path: Path) -> Path:
    resolved = path.expanduser().absolute()
    symlink = _first_symlink_component(resolved.parent)
    if symlink is not None:
        raise TaskStateStoreError(f"task-state store parent contains symlink: {symlink}")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    symlink = _first_symlink_component(resolved.parent)
    if symlink is not None:
        raise TaskStateStoreError(f"task-state store parent contains symlink: {symlink}")
    if resolved.is_symlink() or (resolved.exists() and not resolved.is_file()):
        raise TaskStateStoreError(f"task-state event log must be a regular file: {resolved}")
    return resolved


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@contextmanager
def _store_lock(path: Path, *, shared: bool):
    lock_path = path.with_name(f"{path.name}.lock")
    descriptor = os.open(
        lock_path,
        os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise TaskStateStoreError(f"task-state lock must be a regular file: {lock_path}")
        os.fchmod(descriptor, stat.S_IRUSR | stat.S_IWUSR)
        fcntl.flock(descriptor, fcntl.LOCK_SH if shared else fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def validate_event(event: Any, *, expected_sequence: int, previous_sha256: str | None) -> dict[str, Any]:
    if not isinstance(event, dict):
        raise TaskStateStoreError(f"task-state event {expected_sequence} is not an object")
    required = {
        "version",
        "type",
        "event_id",
        "event_sha256",
        "sequence",
        "committed_at",
        "source",
        "previous_event_sha256",
        "state_sha256",
        "state",
    }
    if set(event) != required:
        raise TaskStateStoreError(
            f"task-state event {expected_sequence} schema mismatch: {sorted(set(event) ^ required)}"
        )
    if event.get("version") != EVENT_VERSION or event.get("type") != EVENT_TYPE_STATE_COMMITTED:
        raise TaskStateStoreError(f"task-state event {expected_sequence} has unsupported version/type")
    if event.get("sequence") != expected_sequence:
        raise TaskStateStoreError(
            f"task-state sequence mismatch: expected {expected_sequence}, got {event.get('sequence')!r}"
        )
    if event.get("previous_event_sha256") != previous_sha256:
        raise TaskStateStoreError(f"task-state event {expected_sequence} previous hash mismatch")
    state = event.get("state")
    if not isinstance(state, dict) or event.get("state_sha256") != sha256_json(state):
        raise TaskStateStoreError(f"task-state event {expected_sequence} state digest mismatch")
    event_sha256 = sha256_json(_event_digest_payload(event))
    if event.get("event_sha256") != event_sha256:
        raise TaskStateStoreError(f"task-state event {expected_sequence} event digest mismatch")
    if event.get("event_id") != f"task-state-{event_sha256}":
        raise TaskStateStoreError(f"task-state event {expected_sequence} event id mismatch")
    if not str(event.get("committed_at") or "").strip() or not str(event.get("source") or "").strip():
        raise TaskStateStoreError(f"task-state event {expected_sequence} lacks commit provenance")
    return event


def _load_events_unlocked(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise TaskStateStoreError(f"task-state event log must be a regular file: {path}")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
    finally:
        os.close(descriptor)
    payload = b"".join(chunks)
    events: list[dict[str, Any]] = []
    previous_sha256: str | None = None
    for line_number, raw_line in enumerate(payload.splitlines(), start=1):
        if not raw_line.strip():
            continue
        try:
            event = json.loads(raw_line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TaskStateStoreError(f"invalid task-state event at line {line_number}: {exc}") from exc
        validated = validate_event(
            event,
            expected_sequence=len(events) + 1,
            previous_sha256=previous_sha256,
        )
        previous_sha256 = str(validated["event_sha256"])
        events.append(validated)
    return events


def load_events(path: str | Path) -> list[dict[str, Any]]:
    event_path = _prepare_parent(Path(path))
    with _store_lock(event_path, shared=True):
        return _load_events_unlocked(event_path)


def project_latest_state(events: Iterable[dict[str, Any]]) -> dict[str, Any]:
    latest: dict[str, Any] | None = None
    previous_sha256: str | None = None
    for sequence, event in enumerate(events, start=1):
        validated = validate_event(
            event,
            expected_sequence=sequence,
            previous_sha256=previous_sha256,
        )
        latest = validated["state"]
        previous_sha256 = str(validated["event_sha256"])
    return copy.deepcopy(latest or {})


def append_state_commit(
    path: str | Path,
    state: dict[str, Any],
    *,
    source: str,
    committed_at: str | None = None,
) -> dict[str, Any]:
    if not isinstance(state, dict):
        raise TaskStateStoreError("task-state commit must contain an object state")
    event_path = _prepare_parent(Path(path))
    with _store_lock(event_path, shared=False):
        events = _load_events_unlocked(event_path)
        state_sha256 = sha256_json(state)
        if events and events[-1].get("state_sha256") == state_sha256:
            return copy.deepcopy(events[-1])
        event: dict[str, Any] = {
            "version": EVENT_VERSION,
            "type": EVENT_TYPE_STATE_COMMITTED,
            "sequence": len(events) + 1,
            "committed_at": committed_at or utc_now(),
            "source": str(source or "unknown").strip() or "unknown",
            "previous_event_sha256": events[-1]["event_sha256"] if events else None,
            "state_sha256": state_sha256,
            "state": copy.deepcopy(state),
        }
        event_sha256 = sha256_json(event)
        event["event_sha256"] = event_sha256
        event["event_id"] = f"task-state-{event_sha256}"
        validate_event(
            event,
            expected_sequence=len(events) + 1,
            previous_sha256=events[-1]["event_sha256"] if events else None,
        )
        descriptor = os.open(
            event_path,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_APPEND
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise TaskStateStoreError(f"task-state event log must be a regular file: {event_path}")
            os.fchmod(descriptor, stat.S_IRUSR | stat.S_IWUSR)
            payload = canonical_json_bytes(event) + b"\n"
            offset = 0
            while offset < len(payload):
                offset += os.write(descriptor, payload[offset:])
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        _fsync_directory(event_path.parent)
        replayed = _load_events_unlocked(event_path)
        if not replayed or replayed[-1] != event:
            raise TaskStateStoreError("task-state append readback mismatch")
        return copy.deepcopy(event)


def verify_projection(path: str | Path, expected_state: dict[str, Any]) -> dict[str, Any]:
    events = load_events(path)
    projected = project_latest_state(events)
    projected_sha256 = sha256_json(projected)
    expected_sha256 = sha256_json(expected_state)
    return {
        "ok": bool(events) and projected_sha256 == expected_sha256,
        "event_count": len(events),
        "last_event_id": events[-1]["event_id"] if events else None,
        "projected_state_sha256": projected_sha256,
        "expected_state_sha256": expected_sha256,
    }
