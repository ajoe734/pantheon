"""Supervisor Authority V2 task-state store.

The authoritative hot-read surface is a small, atomically replaced head file.
The append-only journal contains transition deltas, not repeated copies of the
whole board.  A reader validates the head and replays only bytes appended after
the head's recorded offset.  Full-chain and legacy-archive hashing are explicit
offline verification operations and are never part of a scheduling read.

The public ``append_state_commit``/``load_snapshot`` API is intentionally kept
small so command and supervisor callers can share one authority while their
call sites migrate to typed transitions.
"""
from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import os
import stat
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping


EVENT_VERSION = 2
EVENT_TYPE_STATE_TRANSITION = "task_state_transition"
HEAD_VERSION = 2
HEAD_TYPE = "task_state_head"
HEAD_SUFFIX = ".head.json"
ARCHIVE_ANCHOR_VERSION = 1
ARCHIVE_ANCHOR_TYPE = "legacy_full_state_journal_anchor"
ARCHIVE_ANCHOR_SUFFIX = ".legacy-anchor.json"
# Headless replay exists only for the first journal-first crash window.  A
# multi-gigabyte V1 journal accidentally pointed at V2 must fail before any hot
# path tries to read it.  Normal production genesis is roughly one board.
MAX_HEADLESS_RECOVERY_BYTES = 64 * 1024 * 1024
MAX_TAIL_RECOVERY_BYTES = 64 * 1024 * 1024

TERMINAL_TASK_STATUSES = frozenset(
    {"done", "supersede", "superseded", "cancelled", "canceled"}
)
DRAIN_MARKER_KEY = "task_state_drain"
DRAIN_MARKER_AUDIT_FIELDS = ("reason", "actor", "approved_at")
DRAIN_MARKER_TIMESTAMP_FIELD = "approved_at"
NONTERMINAL_DROP_REJECTION = "task-state nonterminal drop rejected"
REJECTION_ID_SAMPLE = 5


class TaskStateStoreError(RuntimeError):
    """The task-state authority is unsafe, corrupt, or fails validation."""


class HistoricalArchiveUnavailableError(TaskStateStoreError):
    """The immutable V1 archive named by its anchor is not locally available."""


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
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _without_digest(record: Mapping[str, Any], *fields: str) -> dict[str, Any]:
    return {key: value for key, value in record.items() if key not in fields}


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
    if not path.is_absolute():
        raise TaskStateStoreError(
            f"task-state event log path must be an absolute path: {path}"
        )
    resolved = path.expanduser().absolute()
    symlink = _first_symlink_component(resolved.parent)
    if symlink is not None:
        raise TaskStateStoreError(
            f"task-state store parent contains symlink: {symlink}"
        )
    resolved.parent.mkdir(parents=True, exist_ok=True)
    symlink = _first_symlink_component(resolved.parent)
    if symlink is not None:
        raise TaskStateStoreError(
            f"task-state store parent contains symlink: {symlink}"
        )
    if resolved.is_symlink() or (resolved.exists() and not resolved.is_file()):
        raise TaskStateStoreError(
            f"task-state event log must be a regular file: {resolved}"
        )
    return resolved


def _require_existing_authority(path: Path) -> Path:
    if not path.is_absolute():
        raise TaskStateStoreError(
            f"task-state event log path must be an absolute path: {path}"
        )
    resolved = path.expanduser().absolute()
    symlink = _first_symlink_component(resolved.parent)
    if symlink is not None:
        raise TaskStateStoreError(
            f"task-state store parent contains symlink: {symlink}"
        )
    required = (
        (resolved, "event log"),
        (_head_path(resolved), "head"),
        (_lock_path(resolved), "lock"),
    )
    for candidate, label in required:
        try:
            info = os.stat(candidate, follow_symlinks=False)
        except OSError as exc:
            raise TaskStateStoreError(
                f"task-state {label} must be an existing regular file: {candidate}"
            ) from exc
        if not stat.S_ISREG(info.st_mode):
            raise TaskStateStoreError(
                f"task-state {label} must be an existing regular file: {candidate}"
            )
    return resolved


def _head_path(event_path: Path) -> Path:
    return event_path.with_name(f"{event_path.name}{HEAD_SUFFIX}")


def archive_anchor_path(event_path: str | Path) -> Path:
    path = Path(event_path)
    return path.with_name(f"{path.name}{ARCHIVE_ANCHOR_SUFFIX}")


def _lock_path(event_path: Path) -> Path:
    return event_path.with_name(f"{event_path.name}.lock")


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(
        path,
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@contextmanager
def _store_lock(path: Path, *, shared: bool, observational: bool = False):
    if observational and not shared:
        raise TaskStateStoreError("observational task-state locks must be shared")
    lock_path = _lock_path(path)
    flags = (
        (os.O_RDONLY if observational else os.O_RDWR | os.O_CREAT)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as exc:
        raise TaskStateStoreError(
            f"cannot open task-state lock: {lock_path}"
        ) from exc
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise TaskStateStoreError(
                f"task-state lock must be a regular file: {lock_path}"
            )
        if not observational:
            os.fchmod(descriptor, stat.S_IRUSR | stat.S_IWUSR)
        fcntl.flock(descriptor, fcntl.LOCK_SH if shared else fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _read_regular_file(path: Path, *, missing_ok: bool = False) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        if missing_ok:
            return b""
        raise
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise TaskStateStoreError(f"task-state file must be regular: {path}")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _atomic_write_json(path: Path, record: Mapping[str, Any]) -> None:
    temporary = path.with_name(
        f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    )
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(temporary, flags, 0o600)
    try:
        body = canonical_json_bytes(record) + b"\n"
        offset = 0
        while offset < len(body):
            offset += os.write(descriptor, body[offset:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    try:
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _task_rows(state: Any) -> tuple[list[Any], bool]:
    if not isinstance(state, dict):
        return [], False
    if "tasks" not in state:
        return [], True
    tasks = state["tasks"]
    if not isinstance(tasks, list):
        return [], False
    return tasks, True


def _task_identity(task: Any) -> str | None:
    if not isinstance(task, dict):
        return None
    identity = str(task.get("id") or "").strip()
    return identity or None


def _is_terminal_task(task: Any) -> bool:
    if not isinstance(task, dict):
        return False
    return str(task.get("status") or "").strip().lower() in TERMINAL_TASK_STATUSES


def _task_census(state: Any) -> dict[str, Any]:
    rows, container_ok = _task_rows(state)
    terminal_by_id: dict[str, bool] = {}
    unidentified_nonterminal = 0
    for task in rows:
        identity = _task_identity(task)
        terminal = _is_terminal_task(task)
        if identity is None:
            if not terminal:
                unidentified_nonterminal += 1
            continue
        terminal_by_id[identity] = terminal_by_id.get(identity, True) and terminal
    if not container_ok:
        unidentified_nonterminal += 1
    return {
        "ids": set(terminal_by_id),
        "nonterminal_ids": {
            identity
            for identity, terminal in terminal_by_id.items()
            if not terminal
        },
        "unidentified_nonterminal": unidentified_nonterminal,
    }


def _parse_audit_timestamp(value: str) -> datetime | None:
    text = value.strip()
    if text[-1:] in {"z", "Z"}:
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _drain_marker_rejection(
    new_state: dict[str, Any],
    previous_state: Any,
    *,
    removed: list[str],
    unidentified_shortfall: int,
) -> str | None:
    marker = new_state.get(DRAIN_MARKER_KEY)
    if not isinstance(marker, dict):
        return f"no explicit audited {DRAIN_MARKER_KEY} marker was supplied"
    missing = [
        field
        for field in DRAIN_MARKER_AUDIT_FIELDS
        if not isinstance(marker.get(field), str) or not marker[field].strip()
    ]
    if missing:
        return f"{DRAIN_MARKER_KEY} lacks audit fields {missing}"
    approved_at = _parse_audit_timestamp(marker[DRAIN_MARKER_TIMESTAMP_FIELD])
    if approved_at is None:
        return (
            f"{DRAIN_MARKER_KEY}.{DRAIN_MARKER_TIMESTAMP_FIELD} must be a "
            "timezone-aware ISO 8601 timestamp"
        )
    if approved_at > datetime.now(timezone.utc):
        return f"{DRAIN_MARKER_KEY}.{DRAIN_MARKER_TIMESTAMP_FIELD} is in the future"
    if isinstance(previous_state, dict) and previous_state.get(DRAIN_MARKER_KEY) == marker:
        return f"{DRAIN_MARKER_KEY} is an unchanged copy of the previous commit"
    raw_ids = marker.get("task_ids")
    if not isinstance(raw_ids, list) or not all(
        isinstance(item, str) and item.strip() for item in raw_ids
    ):
        return f"{DRAIN_MARKER_KEY}.task_ids must list the removed task ids"
    normalized = [item.strip() for item in raw_ids]
    if len(set(normalized)) != len(normalized):
        return f"{DRAIN_MARKER_KEY}.task_ids repeats task ids"
    authorized = set(normalized)
    current_ids = _task_census(new_state)["ids"]
    if authorized & current_ids:
        return f"{DRAIN_MARKER_KEY} names tasks that are still on the board"
    if authorized != set(removed):
        return f"{DRAIN_MARKER_KEY}.task_ids must equal the removed task ids"
    if unidentified_shortfall and marker.get("allow_unidentified") is not True:
        return (
            f"{DRAIN_MARKER_KEY} must set allow_unidentified for "
            f"{unidentified_shortfall} row(s) without a task id"
        )
    return None


def validate_state_transition(
    new_state: dict[str, Any], previous_state: dict[str, Any] | None
) -> None:
    """Refuse unaudited disappearance of live task identities."""

    if not isinstance(new_state, dict):
        raise TaskStateStoreError("task-state commit must contain an object state")
    if previous_state is None:
        return
    previous = _task_census(previous_state)
    if not previous["nonterminal_ids"] and not previous["unidentified_nonterminal"]:
        return
    current = _task_census(new_state)
    removed = sorted(previous["nonterminal_ids"] - current["ids"])
    unidentified_shortfall = max(
        0,
        previous["unidentified_nonterminal"]
        - current["unidentified_nonterminal"],
    )
    if not removed and not unidentified_shortfall:
        return
    rejection = _drain_marker_rejection(
        new_state,
        previous_state,
        removed=removed,
        unidentified_shortfall=unidentified_shortfall,
    )
    if rejection is None:
        return
    survivors = previous["nonterminal_ids"] & current["ids"]
    mode = "mass replacement" if removed and not survivors else "disappearance"
    detail = ", ".join(removed[:REJECTION_ID_SAMPLE]) or "unidentified rows"
    if len(removed) > REJECTION_ID_SAMPLE:
        detail += f", ... (+{len(removed) - REJECTION_ID_SAMPLE} more)"
    raise TaskStateStoreError(
        f"{NONTERMINAL_DROP_REJECTION}: {mode} would remove "
        f"{len(removed) + unidentified_shortfall} nonterminal task(s) "
        f"[{detail}]; {rejection}"
    )


def _task_index(value: Any) -> tuple[list[str], dict[str, dict[str, Any]]] | None:
    if not isinstance(value, list):
        return None
    order: list[str] = []
    rows: dict[str, dict[str, Any]] = {}
    for row in value:
        identity = _task_identity(row)
        if identity is None or identity in rows or not isinstance(row, dict):
            return None
        order.append(identity)
        rows[identity] = row
    return order, rows


def _dict_ops(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    *,
    prefix: tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    ops: list[dict[str, Any]] = []
    for key in sorted(set(before) - set(after)):
        ops.append({"op": "remove", "path": [*prefix, key]})
    for key in sorted(set(after)):
        path = (*prefix, key)
        if key not in before:
            ops.append({"op": "set", "path": list(path), "value": copy.deepcopy(after[key])})
        elif isinstance(before[key], dict) and isinstance(after[key], dict):
            ops.extend(_dict_ops(before[key], after[key], prefix=path))
        elif before[key] != after[key]:
            ops.append({"op": "set", "path": list(path), "value": copy.deepcopy(after[key])})
    return ops


def build_state_delta(
    previous_state: dict[str, Any], new_state: dict[str, Any]
) -> dict[str, Any]:
    """Return a deterministic compact transition between two board snapshots."""

    before = copy.deepcopy(previous_state)
    after = copy.deepcopy(new_state)
    task_delta: dict[str, Any] | None = None
    before_tasks = _task_index(before.get("tasks")) if "tasks" in before else None
    after_tasks = _task_index(after.get("tasks")) if "tasks" in after else None
    if before_tasks is not None and after_tasks is not None:
        before_order, before_rows = before_tasks
        after_order, after_rows = after_tasks
        removed = sorted(set(before_rows) - set(after_rows))
        upsert = [
            copy.deepcopy(after_rows[identity])
            for identity in after_order
            if before_rows.get(identity) != after_rows[identity]
        ]
        order = after_order if before_order != after_order else None
        if removed or upsert or order is not None:
            task_delta = {"remove": removed, "upsert": upsert}
            if order is not None:
                task_delta["order"] = order
        before.pop("tasks", None)
        after.pop("tasks", None)
    ops = _dict_ops(before, after)
    delta: dict[str, Any] = {"ops": ops}
    if task_delta is not None:
        delta["tasks"] = task_delta
    return delta


def _apply_op(state: dict[str, Any], op: Mapping[str, Any]) -> None:
    if set(op) not in ({"op", "path"}, {"op", "path", "value"}):
        raise TaskStateStoreError("task-state delta operation schema mismatch")
    operation = op.get("op")
    path = op.get("path")
    if operation not in {"set", "remove"} or not isinstance(path, list) or not path:
        raise TaskStateStoreError("task-state delta operation is invalid")
    if not all(isinstance(part, str) and part for part in path):
        raise TaskStateStoreError("task-state delta path is invalid")
    parent: dict[str, Any] = state
    for part in path[:-1]:
        child = parent.get(part)
        if not isinstance(child, dict):
            if operation == "set":
                child = {}
                parent[part] = child
            else:
                raise TaskStateStoreError("task-state delta removes a missing path")
        parent = child
    leaf = path[-1]
    if operation == "remove":
        if leaf not in parent:
            raise TaskStateStoreError("task-state delta removes a missing path")
        del parent[leaf]
    else:
        if "value" not in op:
            raise TaskStateStoreError("task-state set operation lacks a value")
        parent[leaf] = copy.deepcopy(op["value"])


def apply_state_delta(
    previous_state: dict[str, Any], delta: Mapping[str, Any]
) -> dict[str, Any]:
    if not isinstance(delta, dict) or set(delta) - {"ops", "tasks"}:
        raise TaskStateStoreError("task-state delta schema mismatch")
    ops = delta.get("ops")
    if not isinstance(ops, list):
        raise TaskStateStoreError("task-state delta ops must be a list")
    state = copy.deepcopy(previous_state)
    for op in ops:
        if not isinstance(op, dict):
            raise TaskStateStoreError("task-state delta operation must be an object")
        _apply_op(state, op)
    task_delta = delta.get("tasks")
    if task_delta is None:
        return state
    if not isinstance(task_delta, dict) or set(task_delta) - {"remove", "upsert", "order"}:
        raise TaskStateStoreError("task-state task delta schema mismatch")
    indexed = _task_index(state.get("tasks", []))
    if indexed is None:
        raise TaskStateStoreError("task-state task delta requires uniquely identified rows")
    current_order, rows = indexed
    remove = task_delta.get("remove", [])
    upsert = task_delta.get("upsert", [])
    if not isinstance(remove, list) or not all(isinstance(item, str) for item in remove):
        raise TaskStateStoreError("task-state task removal list is invalid")
    if not isinstance(upsert, list):
        raise TaskStateStoreError("task-state task upsert list is invalid")
    for identity in remove:
        if identity not in rows:
            raise TaskStateStoreError("task-state task delta removes a missing task")
        del rows[identity]
        current_order.remove(identity)
    for row in upsert:
        identity = _task_identity(row)
        if identity is None or not isinstance(row, dict):
            raise TaskStateStoreError("task-state task upsert lacks an identity")
        if identity not in rows:
            current_order.append(identity)
        rows[identity] = copy.deepcopy(row)
    order = task_delta.get("order", current_order)
    if not isinstance(order, list) or len(order) != len(set(order)):
        raise TaskStateStoreError("task-state task order is invalid")
    if set(order) != set(rows):
        raise TaskStateStoreError("task-state task order does not cover current tasks")
    state["tasks"] = [rows[identity] for identity in order]
    return state


def _event_digest_payload(event: Mapping[str, Any]) -> dict[str, Any]:
    return _without_digest(event, "event_id", "event_sha256", "state")


def validate_event(
    event: Any,
    *,
    expected_sequence: int,
    previous_sha256: str | None,
    previous_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(event, dict):
        raise TaskStateStoreError(
            f"task-state event {expected_sequence} is not an object"
        )
    required = {
        "version", "type", "event_id", "event_sha256", "sequence",
        "committed_at", "source", "previous_event_sha256",
        "previous_state_sha256", "state_sha256", "archive_anchor_sha256",
        "delta",
    }
    if set(event) not in (required, required | {"state"}):
        raise TaskStateStoreError(
            f"task-state event {expected_sequence} schema mismatch: "
            f"{sorted(set(event) ^ required)}"
        )
    if event.get("version") != EVENT_VERSION or event.get("type") != EVENT_TYPE_STATE_TRANSITION:
        raise TaskStateStoreError(
            f"task-state event {expected_sequence} has unsupported version/type"
        )
    if event.get("sequence") != expected_sequence:
        raise TaskStateStoreError(
            f"task-state sequence mismatch: expected {expected_sequence}, "
            f"got {event.get('sequence')!r}"
        )
    if event.get("previous_event_sha256") != previous_sha256:
        raise TaskStateStoreError(
            f"task-state event {expected_sequence} previous hash mismatch"
        )
    for field in ("previous_state_sha256", "state_sha256"):
        value = event.get(field)
        if not isinstance(value, str) or len(value) != 64:
            raise TaskStateStoreError(
                f"task-state event {expected_sequence} {field} is invalid"
            )
    anchor = event.get("archive_anchor_sha256")
    if anchor is not None and (not isinstance(anchor, str) or len(anchor) != 64):
        raise TaskStateStoreError(
            f"task-state event {expected_sequence} archive anchor is invalid"
        )
    if not isinstance(event.get("delta"), dict):
        raise TaskStateStoreError(
            f"task-state event {expected_sequence} delta is invalid"
        )
    event_sha256 = sha256_json(_event_digest_payload(event))
    if event.get("event_sha256") != event_sha256:
        raise TaskStateStoreError(
            f"task-state event {expected_sequence} event digest mismatch"
        )
    if event.get("event_id") != f"task-state-{event_sha256}":
        raise TaskStateStoreError(
            f"task-state event {expected_sequence} event id mismatch"
        )
    if not str(event.get("committed_at") or "").strip() or not str(event.get("source") or "").strip():
        raise TaskStateStoreError(
            f"task-state event {expected_sequence} lacks commit provenance"
        )
    if previous_state is not None:
        if event["previous_state_sha256"] != sha256_json(previous_state):
            raise TaskStateStoreError(
                f"task-state event {expected_sequence} previous state digest mismatch"
            )
        projected = apply_state_delta(previous_state, event["delta"])
        if event["state_sha256"] != sha256_json(projected):
            raise TaskStateStoreError(
                f"task-state event {expected_sequence} state digest mismatch"
            )
        validate_state_transition(projected, previous_state)
        if "state" in event and event["state"] != projected:
            raise TaskStateStoreError(
                f"task-state event {expected_sequence} derived state mismatch"
            )
    return event


def _head_digest_payload(head: Mapping[str, Any]) -> dict[str, Any]:
    return _without_digest(head, "head_sha256")


def _validate_head(head: Any) -> dict[str, Any]:
    required = {
        "version", "type", "sequence", "event_id", "event_sha256",
        "state_sha256", "state", "journal_bytes", "archive_anchor_sha256",
        "last_event", "updated_at", "head_sha256",
    }
    if not isinstance(head, dict) or set(head) != required:
        raise TaskStateStoreError("task-state head schema mismatch")
    if head.get("version") != HEAD_VERSION or head.get("type") != HEAD_TYPE:
        raise TaskStateStoreError("task-state head version/type is unsupported")
    if head.get("head_sha256") != sha256_json(_head_digest_payload(head)):
        raise TaskStateStoreError("task-state head digest mismatch")
    if not isinstance(head.get("sequence"), int) or head["sequence"] <= 0:
        raise TaskStateStoreError("task-state head sequence is invalid")
    if not isinstance(head.get("journal_bytes"), int) or head["journal_bytes"] <= 0:
        raise TaskStateStoreError("task-state head journal offset is invalid")
    state = head.get("state")
    if not isinstance(state, dict) or head.get("state_sha256") != sha256_json(state):
        raise TaskStateStoreError("task-state head state digest mismatch")
    event = validate_event(
        head.get("last_event"),
        expected_sequence=head["sequence"],
        previous_sha256=head["last_event"].get("previous_event_sha256")
        if isinstance(head.get("last_event"), dict) else None,
    )
    if event["event_id"] != head["event_id"] or event["event_sha256"] != head["event_sha256"]:
        raise TaskStateStoreError("task-state head event identity mismatch")
    if event["state_sha256"] != head["state_sha256"]:
        raise TaskStateStoreError("task-state head/event state mismatch")
    if event["archive_anchor_sha256"] != head["archive_anchor_sha256"]:
        raise TaskStateStoreError("task-state head/event archive anchor mismatch")
    return head


def _read_head(event_path: Path) -> dict[str, Any] | None:
    payload = _read_regular_file(_head_path(event_path), missing_ok=True)
    if not payload:
        return None
    try:
        return _validate_head(json.loads(payload.decode("utf-8")))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TaskStateStoreError(f"invalid task-state head: {exc}") from exc


def _build_head(
    *, event: Mapping[str, Any], state: dict[str, Any], journal_bytes: int
) -> dict[str, Any]:
    raw_event = _without_digest(event, "state")
    head: dict[str, Any] = {
        "version": HEAD_VERSION,
        "type": HEAD_TYPE,
        "sequence": int(event["sequence"]),
        "event_id": str(event["event_id"]),
        "event_sha256": str(event["event_sha256"]),
        "state_sha256": str(event["state_sha256"]),
        "state": copy.deepcopy(state),
        "journal_bytes": journal_bytes,
        "archive_anchor_sha256": event.get("archive_anchor_sha256"),
        "last_event": raw_event,
        "updated_at": utc_now(),
    }
    head["head_sha256"] = sha256_json(head)
    return head


def _journal_size(event_path: Path) -> int:
    try:
        info = os.stat(event_path, follow_symlinks=False)
    except FileNotFoundError:
        return 0
    if not stat.S_ISREG(info.st_mode):
        raise TaskStateStoreError(
            f"task-state event log must be a regular file: {event_path}"
        )
    return int(info.st_size)


def _truncate_journal(event_path: Path, size: int) -> None:
    flags = os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(event_path, flags)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise TaskStateStoreError(
                f"task-state event log must be a regular file: {event_path}"
            )
        os.ftruncate(descriptor, size)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    _fsync_directory(event_path.parent)


def _read_range(event_path: Path, *, offset: int, length: int) -> bytes:
    if length <= 0:
        return b""
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(event_path, flags)
    try:
        chunks: list[bytes] = []
        cursor = offset
        remaining = length
        while remaining:
            chunk = os.pread(descriptor, min(remaining, 1024 * 1024), cursor)
            if not chunk:
                break
            chunks.append(chunk)
            cursor += len(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if len(payload) != length:
            raise TaskStateStoreError("task-state journal changed during read")
        return payload
    finally:
        os.close(descriptor)


def _iter_complete_lines(payload: bytes) -> tuple[list[bytes], int]:
    complete_bytes = payload.rfind(b"\n") + 1
    complete = payload[:complete_bytes]
    lines = [line for line in complete.splitlines() if line.strip()]
    return lines, complete_bytes


def _replay_payload(
    payload: bytes,
    *,
    state: dict[str, Any],
    event_count: int,
    previous_event_sha256: str | None,
    archive_anchor_sha256: str | None,
) -> tuple[dict[str, Any], dict[str, Any] | None, int, int]:
    lines, complete_bytes = _iter_complete_lines(payload)
    current = copy.deepcopy(state)
    last_event: dict[str, Any] | None = None
    for raw_line in lines:
        try:
            event = json.loads(raw_line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TaskStateStoreError(
                f"invalid task-state event at sequence {event_count + 1}: {exc}"
            ) from exc
        if isinstance(event, dict) and event.get("version") == 1:
            raise TaskStateStoreError(
                "legacy full-state journal requires explicit V2 migration"
            )
        validated = validate_event(
            event,
            expected_sequence=event_count + 1,
            previous_sha256=previous_event_sha256,
            previous_state=current,
        )
        if event_count == 0 and previous_event_sha256 is None and archive_anchor_sha256 is None:
            archive_anchor_sha256 = validated["archive_anchor_sha256"]
        if validated["archive_anchor_sha256"] != archive_anchor_sha256:
            raise TaskStateStoreError("task-state archive anchor changed within journal")
        current = apply_state_delta(current, validated["delta"])
        event_count += 1
        previous_event_sha256 = str(validated["event_sha256"])
        last_event = validated
    return current, last_event, event_count, complete_bytes


def _empty_snapshot() -> dict[str, Any]:
    return {
        "event_count": 0,
        "last_event": None,
        "last_event_id": None,
        "last_event_sha256": None,
        "state": {},
        "state_sha256": sha256_json({}),
        "byte_size": 0,
        "revalidated_events": 0,
        "resumed_from_checkpoint": False,
        "resumed_from_head": False,
        "archive_anchor_sha256": None,
        "ignored_partial_tail_bytes": 0,
    }


def _snapshot_from_head_and_tail(
    event_path: Path, *, repair: bool
) -> dict[str, Any]:
    head = _read_head(event_path)
    size = _journal_size(event_path)
    if head is None:
        if size == 0:
            return _empty_snapshot()
        if size > MAX_HEADLESS_RECOVERY_BYTES:
            raise TaskStateStoreError(
                "task-state head is missing and journal exceeds the bounded "
                "genesis-recovery window; explicit V2 migration or offline "
                "recovery is required"
            )
        base = _empty_snapshot()
        offset = 0
    else:
        offset = int(head["journal_bytes"])
        if size < offset:
            raise TaskStateStoreError("task-state journal is shorter than its head")
        base = {
            "event_count": int(head["sequence"]),
            "last_event": head["last_event"],
            "last_event_id": head["event_id"],
            "last_event_sha256": head["event_sha256"],
            "state": copy.deepcopy(head["state"]),
            "state_sha256": head["state_sha256"],
            "byte_size": offset,
            "archive_anchor_sha256": head["archive_anchor_sha256"],
        }
    if size - offset > MAX_TAIL_RECOVERY_BYTES:
        raise TaskStateStoreError(
            "task-state journal tail exceeds the bounded hot-recovery window; "
            "offline audit and head repair are required"
        )
    tail = _read_range(event_path, offset=offset, length=size - offset)
    state, last_event, event_count, complete_bytes = _replay_payload(
        tail,
        state=base["state"],
        event_count=int(base["event_count"]),
        previous_event_sha256=base["last_event_sha256"],
        archive_anchor_sha256=base["archive_anchor_sha256"],
    )
    partial = len(tail) - complete_bytes
    final_bytes = offset + complete_bytes
    if partial and repair:
        _truncate_journal(event_path, final_bytes)
    if last_event is not None and repair:
        _atomic_write_json(
            _head_path(event_path),
            _build_head(event=last_event, state=state, journal_bytes=final_bytes),
        )
    effective_event = last_event or base["last_event"]
    effective_anchor = (
        effective_event.get("archive_anchor_sha256")
        if effective_event is not None
        else base["archive_anchor_sha256"]
    )
    return {
        "event_count": event_count,
        "last_event": copy.deepcopy(effective_event),
        "last_event_id": effective_event.get("event_id") if effective_event else None,
        "last_event_sha256": effective_event.get("event_sha256") if effective_event else None,
        "state": copy.deepcopy(state),
        "state_sha256": sha256_json(state),
        "byte_size": final_bytes,
        "revalidated_events": event_count - int(base["event_count"]),
        "resumed_from_checkpoint": head is not None,
        "resumed_from_head": head is not None,
        "archive_anchor_sha256": effective_anchor,
        "ignored_partial_tail_bytes": partial,
    }


def _public_snapshot(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    value = copy.deepcopy(dict(snapshot))
    if isinstance(value.get("last_event"), dict):
        value["last_event"]["state"] = copy.deepcopy(value["state"])
    return value


def load_snapshot(
    path: str | Path, *, refresh_checkpoint: bool = True
) -> dict[str, Any]:
    """Read the V2 head and, only when needed, replay its uncommitted tail.

    ``refresh_checkpoint`` remains as a compatibility argument.  False selects
    a strictly observational read requiring provisioned journal/head/lock files.
    V2 has no checkpoint and a read never hashes the historical prefix.
    """

    observational = not refresh_checkpoint
    event_path = (
        _require_existing_authority(Path(path))
        if observational
        else _prepare_parent(Path(path))
    )
    with _store_lock(event_path, shared=True, observational=observational):
        return _public_snapshot(
            _snapshot_from_head_and_tail(event_path, repair=False)
        )


class SnapshotTransaction:
    def __init__(self, event_path: Path, snapshot: dict[str, Any]) -> None:
        self._event_path = event_path
        self._snapshot = snapshot

    def load_snapshot(self) -> dict[str, Any]:
        return _public_snapshot(self._snapshot)

    def append_state_commit(
        self,
        state: dict[str, Any],
        *,
        source: str,
        committed_at: str | None = None,
    ) -> dict[str, Any]:
        event, self._snapshot = _append_state_commit_unlocked(
            self._event_path,
            state,
            source=source,
            committed_at=committed_at,
            snapshot=self._snapshot,
        )
        return copy.deepcopy(event)


@contextmanager
def snapshot_transaction(path: str | Path):
    event_path = _prepare_parent(Path(path))
    with _store_lock(event_path, shared=False):
        snapshot = _snapshot_from_head_and_tail(event_path, repair=True)
        yield SnapshotTransaction(event_path, snapshot)


def _read_archive_anchor_digest(event_path: Path) -> str | None:
    payload = _read_regular_file(archive_anchor_path(event_path), missing_ok=True)
    if not payload:
        return None
    try:
        anchor = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TaskStateStoreError(f"invalid legacy archive anchor: {exc}") from exc
    return validate_archive_anchor(anchor)["anchor_sha256"]


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
        snapshot = _snapshot_from_head_and_tail(event_path, repair=True)
        event, _ = _append_state_commit_unlocked(
            event_path,
            state,
            source=source,
            committed_at=committed_at,
            snapshot=snapshot,
        )
        return copy.deepcopy(event)


def _append_state_commit_unlocked(
    event_path: Path,
    state: dict[str, Any],
    *,
    source: str,
    committed_at: str | None,
    snapshot: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    previous_state = snapshot["state"]
    validate_state_transition(state, previous_state if snapshot["event_count"] else None)
    state_sha256 = sha256_json(state)
    if snapshot["event_count"] and state_sha256 == snapshot["state_sha256"]:
        existing = copy.deepcopy(snapshot["last_event"])
        existing["state"] = copy.deepcopy(state)
        return existing, snapshot
    anchor_sha256 = snapshot.get("archive_anchor_sha256")
    if not snapshot["event_count"]:
        anchor_sha256 = _read_archive_anchor_digest(event_path)
    sequence = int(snapshot["event_count"]) + 1
    delta = build_state_delta(previous_state, state)
    event: dict[str, Any] = {
        "version": EVENT_VERSION,
        "type": EVENT_TYPE_STATE_TRANSITION,
        "sequence": sequence,
        "committed_at": committed_at or utc_now(),
        "source": str(source or "unknown").strip() or "unknown",
        "previous_event_sha256": snapshot["last_event_sha256"],
        "previous_state_sha256": sha256_json(previous_state),
        "state_sha256": state_sha256,
        "archive_anchor_sha256": anchor_sha256,
        "delta": delta,
    }
    event_sha256 = sha256_json(event)
    event["event_sha256"] = event_sha256
    event["event_id"] = f"task-state-{event_sha256}"
    validate_event(
        event,
        expected_sequence=sequence,
        previous_sha256=snapshot["last_event_sha256"],
        previous_state=previous_state,
    )
    payload = canonical_json_bytes(event) + b"\n"
    flags = (
        os.O_WRONLY | os.O_CREAT | os.O_APPEND
        | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(event_path, flags, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise TaskStateStoreError(
                f"task-state event log must be a regular file: {event_path}"
            )
        os.fchmod(descriptor, stat.S_IRUSR | stat.S_IWUSR)
        offset = 0
        while offset < len(payload):
            offset += os.write(descriptor, payload[offset:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    journal_bytes = int(snapshot["byte_size"]) + len(payload)
    _atomic_write_json(
        _head_path(event_path),
        _build_head(event=event, state=state, journal_bytes=journal_bytes),
    )
    public_event = copy.deepcopy(event)
    public_event["state"] = copy.deepcopy(state)
    next_snapshot = {
        "event_count": sequence,
        "last_event": public_event,
        "last_event_id": event["event_id"],
        "last_event_sha256": event_sha256,
        "state": copy.deepcopy(state),
        "state_sha256": state_sha256,
        "byte_size": journal_bytes,
        "revalidated_events": 0,
        "resumed_from_checkpoint": True,
        "resumed_from_head": True,
        "archive_anchor_sha256": anchor_sha256,
        "ignored_partial_tail_bytes": 0,
    }
    return public_event, next_snapshot


def _iter_journal_records(event_path: Path) -> Iterator[tuple[int, bytes]]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(event_path, flags)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise TaskStateStoreError(
                f"task-state event log must be a regular file: {event_path}"
            )
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            for line_number, raw_line in enumerate(stream, start=1):
                if raw_line.strip():
                    if not raw_line.endswith(b"\n"):
                        raise TaskStateStoreError(
                            "task-state journal ends with an unterminated event"
                        )
                    yield line_number, raw_line
    finally:
        os.close(descriptor)


def load_events(path: str | Path) -> list[dict[str, Any]]:
    """Offline full replay, returning derived ``state`` for old diagnostics."""

    event_path = _prepare_parent(Path(path))
    with _store_lock(event_path, shared=True):
        if _journal_size(event_path) == 0:
            return []
        events: list[dict[str, Any]] = []
        state: dict[str, Any] = {}
        previous_sha256: str | None = None
        anchor_sha256: str | None = None
        for _, raw_line in _iter_journal_records(event_path):
            try:
                event = json.loads(raw_line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise TaskStateStoreError(f"invalid task-state event: {exc}") from exc
            if isinstance(event, dict) and event.get("version") == 1:
                raise TaskStateStoreError(
                    "legacy full-state journal requires explicit V2 migration"
                )
            validated = validate_event(
                event,
                expected_sequence=len(events) + 1,
                previous_sha256=previous_sha256,
                previous_state=state,
            )
            if not events:
                anchor_sha256 = validated["archive_anchor_sha256"]
            elif validated["archive_anchor_sha256"] != anchor_sha256:
                raise TaskStateStoreError("task-state archive anchor changed within journal")
            state = apply_state_delta(state, validated["delta"])
            materialized = copy.deepcopy(validated)
            materialized["state"] = copy.deepcopy(state)
            events.append(materialized)
            previous_sha256 = str(validated["event_sha256"])
        return events


def project_latest_state(events: Iterable[dict[str, Any]]) -> dict[str, Any]:
    state: dict[str, Any] = {}
    previous_sha256: str | None = None
    for sequence, event in enumerate(events, start=1):
        validated = validate_event(
            event,
            expected_sequence=sequence,
            previous_sha256=previous_sha256,
            previous_state=state,
        )
        state = apply_state_delta(state, validated["delta"])
        previous_sha256 = str(validated["event_sha256"])
    return copy.deepcopy(state)


def verify_snapshot(
    snapshot: dict[str, Any], expected_state: dict[str, Any]
) -> dict[str, Any]:
    projected_sha256 = str(snapshot["state_sha256"])
    expected_sha256 = sha256_json(expected_state)
    census = _task_census(snapshot["state"])
    return {
        "ok": bool(snapshot["event_count"])
        and projected_sha256 == expected_sha256,
        "event_count": int(snapshot["event_count"]),
        "last_event_id": snapshot["last_event_id"],
        "projected_state_sha256": projected_sha256,
        "expected_state_sha256": expected_sha256,
        "nonterminal_task_count": len(census["nonterminal_ids"])
        + census["unidentified_nonterminal"],
        "resumed_from_head": bool(snapshot.get("resumed_from_head")),
        "replayed_tail_events": int(snapshot.get("revalidated_events", 0)),
        "archive_anchor_sha256": snapshot.get("archive_anchor_sha256"),
    }


def verify_projection(
    path: str | Path, expected_state: dict[str, Any]
) -> dict[str, Any]:
    return verify_snapshot(load_snapshot(path), expected_state)


def audit_full_journal(path: str | Path) -> dict[str, Any]:
    """Explicitly replay every V2 event; never called by a hot read."""

    event_path = _require_existing_authority(Path(path))
    with _store_lock(event_path, shared=True, observational=True):
        digest = hashlib.sha256()
        state: dict[str, Any] = {}
        previous_sha256: str | None = None
        event_count = 0
        last_event: dict[str, Any] | None = None
        anchor_sha256: str | None = None
        for _, raw_line in _iter_journal_records(event_path):
            digest.update(raw_line)
            try:
                event = json.loads(raw_line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise TaskStateStoreError(f"invalid task-state event: {exc}") from exc
            validated = validate_event(
                event,
                expected_sequence=event_count + 1,
                previous_sha256=previous_sha256,
                previous_state=state,
            )
            if event_count == 0:
                anchor_sha256 = validated["archive_anchor_sha256"]
            elif validated["archive_anchor_sha256"] != anchor_sha256:
                raise TaskStateStoreError("task-state archive anchor changed within journal")
            state = apply_state_delta(state, validated["delta"])
            previous_sha256 = str(validated["event_sha256"])
            last_event = validated
            event_count += 1
        if not event_count:
            raise TaskStateStoreError("task-state journal is empty")
        head = _read_head(event_path)
        if head is None:
            raise TaskStateStoreError("task-state head is missing")
        if head["sequence"] != event_count or head["event_sha256"] != previous_sha256:
            raise TaskStateStoreError("task-state head is not at the journal tip")
        if head["state_sha256"] != sha256_json(state):
            raise TaskStateStoreError("task-state head projection mismatch")
        return {
            "ok": True,
            "event_count": event_count,
            "last_event_id": last_event["event_id"] if last_event else None,
            "state": state,
            "state_sha256": sha256_json(state),
            "journal_sha256": digest.hexdigest(),
            "byte_size": _journal_size(event_path),
            "archive_anchor_sha256": anchor_sha256,
        }


def validate_archive_anchor(anchor: Any) -> dict[str, Any]:
    required = {
        "version", "type", "archived_path", "byte_size", "journal_sha256",
        "event_count", "last_event_id", "last_event_sha256", "state_sha256",
        "created_at", "anchor_sha256",
    }
    if not isinstance(anchor, dict) or set(anchor) != required:
        raise TaskStateStoreError("legacy archive anchor schema mismatch")
    if anchor.get("version") != ARCHIVE_ANCHOR_VERSION or anchor.get("type") != ARCHIVE_ANCHOR_TYPE:
        raise TaskStateStoreError("legacy archive anchor version/type is unsupported")
    if anchor.get("anchor_sha256") != sha256_json(_without_digest(anchor, "anchor_sha256")):
        raise TaskStateStoreError("legacy archive anchor digest mismatch")
    if not isinstance(anchor.get("byte_size"), int) or anchor["byte_size"] <= 0:
        raise TaskStateStoreError("legacy archive anchor byte size is invalid")
    if not isinstance(anchor.get("event_count"), int) or anchor["event_count"] <= 0:
        raise TaskStateStoreError("legacy archive anchor event count is invalid")
    return anchor


def write_archive_anchor(event_path: str | Path, anchor: Mapping[str, Any]) -> dict[str, Any]:
    path = _prepare_parent(Path(event_path))
    record = dict(anchor)
    record["anchor_sha256"] = sha256_json(_without_digest(record, "anchor_sha256"))
    validated = validate_archive_anchor(record)
    target = archive_anchor_path(path)
    existing_payload = _read_regular_file(target, missing_ok=True)
    if existing_payload:
        try:
            existing = validate_archive_anchor(json.loads(existing_payload.decode("utf-8")))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TaskStateStoreError(f"invalid existing legacy archive anchor: {exc}") from exc
        if existing != validated:
            raise TaskStateStoreError("legacy archive anchor is immutable")
        return copy.deepcopy(existing)
    _atomic_write_json(target, validated)
    return copy.deepcopy(validated)


def verify_archive_anchor(
    event_path: str | Path,
    *,
    archive_path: str | Path | None = None,
) -> dict[str, Any]:
    path = Path(event_path).expanduser().absolute()
    payload = _read_regular_file(archive_anchor_path(path))
    try:
        anchor = validate_archive_anchor(json.loads(payload.decode("utf-8")))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TaskStateStoreError(f"invalid legacy archive anchor: {exc}") from exc
    archived = Path(archive_path or anchor["archived_path"]).expanduser().absolute()
    digest = hashlib.sha256()
    byte_size = 0
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(archived, flags)
    except FileNotFoundError as exc:
        raise HistoricalArchiveUnavailableError(
            "immutable legacy archive is unavailable; restore the exact anchored "
            f"bytes or pass --archive-path to their relocated file: {archived}"
        ) from exc
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise TaskStateStoreError("legacy archive is not a regular file")
        while True:
            chunk = os.read(descriptor, 16 * 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            byte_size += len(chunk)
    finally:
        os.close(descriptor)
    snapshot = load_snapshot(path, refresh_checkpoint=False)
    bound_anchor = snapshot.get("archive_anchor_sha256")
    ok = (
        anchor["anchor_sha256"] == bound_anchor
        and byte_size == anchor["byte_size"]
        and digest.hexdigest() == anchor["journal_sha256"]
    )
    return {
        "ok": ok,
        "anchor_sha256": anchor["anchor_sha256"],
        "bound_anchor_sha256": bound_anchor,
        "archived_path": anchor["archived_path"],
        "audited_path": str(archived),
        "byte_size": byte_size,
        "expected_byte_size": anchor["byte_size"],
        "journal_sha256": digest.hexdigest(),
        "expected_journal_sha256": anchor["journal_sha256"],
    }
