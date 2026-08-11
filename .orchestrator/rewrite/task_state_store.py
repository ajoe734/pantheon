"""Authoritative Supervisor V2 task-state store.

The live store has exactly two mutable artifacts: a compact current head and
an append-only JSONL transition log.  A mutation fsyncs its transition before
atomically replacing the head.  Consequently a crash can leave at most the
small transition tail after the last head; reads recover that tail without
opening, hashing, or parsing the frozen V1 archive.

The V1 full-board journal is deliberately *not* a runtime compatibility path.
``migrate_legacy_journal`` freezes it as an auditable archive and writes a V2
genesis transition bound to the archive identity.  Archive verification belongs
to ``verify_full_chain`` and the offline verification command only.
"""
from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import os
import stat
import tempfile
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator


EVENT_VERSION = 2
EVENT_TYPE_STATE_COMMITTED = "task_state_delta_committed"
HEAD_VERSION = 2
HEAD_TYPE = "task_state_current_head"
ARCHIVE_VERSION = 1
ARCHIVE_TYPE = "task_state_v1_archive"
LEGACY_ARCHIVE_READ_CHUNK_BYTES = 64 * 1024

# Kept as the public safety vocabulary used by the governed status writer.
TERMINAL_TASK_STATUSES = frozenset({"done", "supersede", "superseded", "cancelled", "canceled"})
DRAIN_MARKER_KEY = "task_state_drain"
DRAIN_MARKER_AUDIT_FIELDS = ("reason", "actor", "approved_at")
DRAIN_MARKER_TIMESTAMP_FIELD = "approved_at"
NONTERMINAL_DROP_REJECTION = "task-state nonterminal drop rejected"
REJECTION_ID_SAMPLE = 5


class TaskStateStoreError(RuntimeError):
    """The V2 store is malformed, stale, or unsafe to use."""


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


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
    """Validate a writable absolute runtime path without following symlinks."""

    if not path.is_absolute():
        raise TaskStateStoreError(f"task-state event log path must be an absolute path: {path}")
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


def _require_existing_parent(path: Path) -> Path:
    if not path.is_absolute():
        raise TaskStateStoreError(f"task-state event log path must be an absolute path: {path}")
    resolved = path.expanduser().absolute()
    symlink = _first_symlink_component(resolved.parent)
    if symlink is not None:
        raise TaskStateStoreError(f"task-state store parent contains symlink: {symlink}")
    try:
        info = os.stat(resolved.parent, follow_symlinks=False)
    except OSError as exc:
        raise TaskStateStoreError(f"task-state store parent must already exist: {resolved.parent}") from exc
    if not stat.S_ISDIR(info.st_mode):
        raise TaskStateStoreError(f"task-state store parent must be a directory: {resolved.parent}")
    return resolved


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _head_path(event_path: Path) -> Path:
    return event_path.with_name(f"{event_path.name}.head.json")


def _archive_path(event_path: Path) -> Path:
    return event_path.with_name(f"{event_path.name}.v1.archive.jsonl")


def _archive_manifest_path(event_path: Path) -> Path:
    return event_path.with_name(f"{event_path.name}.archive.json")


def _lock_path(event_path: Path) -> Path:
    return event_path.with_name(f"{event_path.name}.lock")


@contextmanager
def _store_lock(event_path: Path, *, shared: bool, observational: bool = False) -> Iterator[None]:
    if observational and not shared:
        raise TaskStateStoreError("observational task-state locks must be shared")
    lock_path = _lock_path(event_path)
    flags = (os.O_RDONLY if observational else os.O_RDWR | os.O_CREAT) | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except FileNotFoundError:
        if observational:
            raise TaskStateStoreError(
                f"task-state lock must be an existing regular file: {lock_path}"
            ) from None
        raise TaskStateStoreError(f"cannot open task-state lock: {lock_path}") from None
    except OSError as exc:
        raise TaskStateStoreError(f"cannot open task-state lock: {lock_path}") from exc
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise TaskStateStoreError(f"task-state lock must be a regular file: {lock_path}")
        if not observational:
            os.fchmod(descriptor, stat.S_IRUSR | stat.S_IWUSR)
        fcntl.flock(descriptor, fcntl.LOCK_SH if shared else fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _read_regular_bytes(path: Path, *, label: str, missing_ok: bool = False) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        if missing_ok:
            return b""
        raise TaskStateStoreError(f"task-state {label} is missing: {path}") from None
    except OSError as exc:
        raise TaskStateStoreError(f"cannot open task-state {label}: {path}") from exc
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise TaskStateStoreError(f"task-state {label} must be a regular file: {path}")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _event_digest_payload(event: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in event.items() if key not in {"event_id", "event_sha256"}}


def _head_digest_payload(head: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in head.items() if key != "head_sha256"}


def _archive_digest_payload(manifest: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in manifest.items() if key != "manifest_sha256"}


def _validate_sha256(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise TaskStateStoreError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _validate_path(path: Any) -> list[str | int]:
    if not isinstance(path, list) or any(not isinstance(part, (str, int)) or isinstance(part, bool) for part in path):
        raise TaskStateStoreError("task-state delta path must be a list of string keys or integer indexes")
    if any(isinstance(part, int) and part < 0 for part in path):
        raise TaskStateStoreError("task-state delta indexes must be non-negative")
    return path


def _validate_delta(delta: Any) -> list[dict[str, Any]]:
    if not isinstance(delta, list):
        raise TaskStateStoreError("task-state delta must be a list")
    normalized: list[dict[str, Any]] = []
    seen: set[bytes] = set()
    for operation in delta:
        if not isinstance(operation, dict):
            raise TaskStateStoreError("task-state delta operation must be an object")
        kind = operation.get("op")
        if kind == "set":
            if set(operation) != {"op", "path", "value"}:
                raise TaskStateStoreError("task-state set delta schema mismatch")
        elif kind == "delete":
            if set(operation) != {"op", "path"}:
                raise TaskStateStoreError("task-state delete delta schema mismatch")
            if not operation.get("path"):
                raise TaskStateStoreError("task-state delta cannot delete the state root")
        elif kind == "insert":
            if set(operation) != {"op", "path", "value"}:
                raise TaskStateStoreError("task-state insert delta schema mismatch")
            if not operation.get("path"):
                raise TaskStateStoreError("task-state delta cannot insert at the state root")
        elif kind == "remove":
            if set(operation) != {"op", "path"}:
                raise TaskStateStoreError("task-state remove delta schema mismatch")
            if not operation.get("path"):
                raise TaskStateStoreError("task-state delta cannot remove the state root")
        else:
            raise TaskStateStoreError("task-state delta operation must be set, delete, insert, or remove")
        path = _validate_path(operation.get("path"))
        # A remove followed by an insert at the same list position is a
        # deterministic replacement after list reindexing.  Reject only exact
        # duplicate operations, rather than making that compact form invalid.
        identity = canonical_json_bytes({"op": kind, "path": path})
        if identity in seen:
            raise TaskStateStoreError("task-state delta repeats an operation path")
        seen.add(identity)
        normalized.append(copy.deepcopy(operation))
    return normalized


def _walk_parent(state: Any, path: list[str | int]) -> tuple[Any, str | int]:
    if not path:
        raise TaskStateStoreError("task-state delta root has no parent")
    current = state
    for part in path[:-1]:
        if isinstance(current, dict) and isinstance(part, str):
            if part not in current:
                raise TaskStateStoreError(f"task-state delta path is missing: {path!r}")
            current = current[part]
        elif isinstance(current, list) and isinstance(part, int) and part < len(current):
            current = current[part]
        else:
            raise TaskStateStoreError(f"task-state delta path has incompatible container: {path!r}")
    return current, path[-1]


def apply_delta(previous_state: dict[str, Any], delta: Any) -> dict[str, Any]:
    """Apply a validated deterministic delta without mutating the predecessor."""

    if not isinstance(previous_state, dict):
        raise TaskStateStoreError("task-state predecessor must be an object")
    result: Any = copy.deepcopy(previous_state)
    for operation in _validate_delta(delta):
        path = operation["path"]
        if not path:
            if operation["op"] != "set" or not isinstance(operation["value"], dict):
                raise TaskStateStoreError("task-state root replacement must set an object")
            result = copy.deepcopy(operation["value"])
            continue
        parent, leaf = _walk_parent(result, path)
        if operation["op"] == "set":
            if isinstance(parent, dict) and isinstance(leaf, str):
                parent[leaf] = copy.deepcopy(operation["value"])
            elif isinstance(parent, list) and isinstance(leaf, int) and leaf < len(parent):
                parent[leaf] = copy.deepcopy(operation["value"])
            else:
                raise TaskStateStoreError(f"task-state set delta has incompatible target: {path!r}")
        elif operation["op"] == "delete":
            if isinstance(parent, dict) and isinstance(leaf, str) and leaf in parent:
                del parent[leaf]
            elif isinstance(parent, list) and isinstance(leaf, int) and leaf < len(parent):
                del parent[leaf]
            else:
                raise TaskStateStoreError(f"task-state delete delta has missing target: {path!r}")
        elif operation["op"] == "insert":
            if isinstance(parent, list) and isinstance(leaf, int) and leaf <= len(parent):
                parent.insert(leaf, copy.deepcopy(operation["value"]))
            else:
                raise TaskStateStoreError(f"task-state insert delta has incompatible target: {path!r}")
        elif isinstance(parent, list) and isinstance(leaf, int) and leaf < len(parent):
            # ``remove`` deliberately has list-only semantics.  Keep ``delete``
            # for compatibility with existing object-key and list-index deltas.
            del parent[leaf]
        else:
            raise TaskStateStoreError(f"task-state remove delta has incompatible target: {path!r}")
    if not isinstance(result, dict):
        raise TaskStateStoreError("task-state delta did not produce an object")
    return result


def _state_delta(previous: Any, current: Any, path: list[str | int] | None = None) -> list[dict[str, Any]]:
    """Produce compact, deterministic deltas for ordinary board mutations."""

    path = [] if path is None else path
    if type(previous) is not type(current):
        return [{"op": "set", "path": path, "value": copy.deepcopy(current)}]
    if isinstance(previous, dict):
        operations: list[dict[str, Any]] = []
        for key in sorted(set(previous) - set(current)):
            operations.append({"op": "delete", "path": [*path, key]})
        for key in sorted(set(current) - set(previous)):
            operations.append({"op": "set", "path": [*path, key], "value": copy.deepcopy(current[key])})
        for key in sorted(set(previous) & set(current)):
            operations.extend(_state_delta(previous[key], current[key], [*path, key]))
        return operations
    if isinstance(previous, list):
        if len(previous) != len(current):
            # List length changes are normally task materialization or archive
            # removal.  Retain an unchanged prefix/suffix and mutate only the
            # affected span so a 1,200-row board does not serialize again just
            # because one task row was added or archived.
            prefix = 0
            common = min(len(previous), len(current))
            while prefix < common and previous[prefix] == current[prefix]:
                prefix += 1
            suffix = 0
            while (
                suffix < len(previous) - prefix
                and suffix < len(current) - prefix
                and previous[len(previous) - suffix - 1] == current[len(current) - suffix - 1]
            ):
                suffix += 1
            operations: list[dict[str, Any]] = []
            # Remove from the right so each index remains valid while the
            # predecessor span contracts.
            for index in range(len(previous) - suffix - 1, prefix - 1, -1):
                operations.append({"op": "remove", "path": [*path, index]})
            # Insert from the left so each following index addresses the
            # materialized list after the preceding insertion.
            for offset, value in enumerate(current[prefix : len(current) - suffix]):
                operations.append(
                    {
                        "op": "insert",
                        "path": [*path, prefix + offset],
                        "value": copy.deepcopy(value),
                    }
                )
            return operations
        operations = []
        for index, (before, after) in enumerate(zip(previous, current)):
            operations.extend(_state_delta(before, after, [*path, index]))
        return operations
    if previous != current:
        return [{"op": "set", "path": path, "value": copy.deepcopy(current)}]
    return []


def _task_rows(state: Any) -> tuple[list[Any], bool]:
    if not isinstance(state, dict):
        return [], False
    if "tasks" not in state:
        return [], True
    tasks = state["tasks"]
    return (tasks, True) if isinstance(tasks, list) else ([], False)


def _task_identity(task: Any) -> str | None:
    if not isinstance(task, dict):
        return None
    identity = str(task.get("id") or "").strip()
    return identity or None


def _is_terminal_task(task: Any) -> bool:
    return isinstance(task, dict) and str(task.get("status") or "").strip().lower() in TERMINAL_TASK_STATUSES


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
        "nonterminal_ids": {identity for identity, terminal in terminal_by_id.items() if not terminal},
        "unidentified_nonterminal": unidentified_nonterminal,
    }


def nonterminal_task_ids(state: Any) -> set[str]:
    return set(_task_census(state)["nonterminal_ids"])


def _parse_audit_timestamp(value: str) -> datetime | None:
    text = value.strip()
    if text[-1:] in {"z", "Z"}:
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None and parsed.utcoffset() is not None else None


def _drain_marker_rejection(new_state: dict[str, Any], previous_state: Any, *, removed: list[str], unidentified_shortfall: int) -> str | None:
    marker = new_state.get(DRAIN_MARKER_KEY)
    if marker is None:
        return f"no explicit audited {DRAIN_MARKER_KEY} marker was supplied"
    if not isinstance(marker, dict):
        return f"{DRAIN_MARKER_KEY} must be an object"
    missing = [field for field in DRAIN_MARKER_AUDIT_FIELDS if not isinstance(marker.get(field), str) or not marker[field].strip()]
    if missing:
        return f"{DRAIN_MARKER_KEY} lacks audit fields {missing}"
    approved_at = _parse_audit_timestamp(marker[DRAIN_MARKER_TIMESTAMP_FIELD])
    if approved_at is None:
        return f"{DRAIN_MARKER_KEY}.{DRAIN_MARKER_TIMESTAMP_FIELD} must be a timezone-aware ISO 8601 timestamp"
    if approved_at > datetime.now(timezone.utc):
        return f"{DRAIN_MARKER_KEY}.{DRAIN_MARKER_TIMESTAMP_FIELD} is in the future"
    if isinstance(previous_state, dict) and previous_state.get(DRAIN_MARKER_KEY) == marker:
        return f"{DRAIN_MARKER_KEY} is an unchanged copy of the previous commit"
    raw_ids = marker.get("task_ids")
    if not isinstance(raw_ids, list) or not all(isinstance(item, str) and item.strip() for item in raw_ids):
        return f"{DRAIN_MARKER_KEY}.task_ids must list the removed task ids"
    authorized = [item.strip() for item in raw_ids]
    duplicates = sorted({item for item in authorized if authorized.count(item) > 1})
    if duplicates:
        return f"{DRAIN_MARKER_KEY}.task_ids repeats task ids: {duplicates}"
    authorized_set = set(authorized)
    still_present = sorted(authorized_set & _task_census(new_state)["ids"])
    if still_present:
        return f"{DRAIN_MARKER_KEY} names tasks that are still on the board: {still_present}"
    uncovered = sorted(set(removed) - authorized_set)
    if uncovered:
        return f"{DRAIN_MARKER_KEY} does not cover removed tasks: {uncovered}"
    unrelated = sorted(authorized_set - set(removed))
    if unrelated:
        return f"{DRAIN_MARKER_KEY} names tasks that were not live removals in this commit: {unrelated}"
    if unidentified_shortfall and marker.get("allow_unidentified") is not True:
        return f"{DRAIN_MARKER_KEY} must set allow_unidentified for {unidentified_shortfall} row(s) without a task id"
    return None


def validate_state_transition(new_state: dict[str, Any], previous_state: dict[str, Any] | None) -> None:
    if not isinstance(new_state, dict):
        raise TaskStateStoreError("task-state commit must contain an object state")
    if previous_state is None:
        return
    previous = _task_census(previous_state)
    if not previous["nonterminal_ids"] and not previous["unidentified_nonterminal"]:
        return
    current = _task_census(new_state)
    removed = sorted(previous["nonterminal_ids"] - current["ids"])
    unidentified_shortfall = max(0, previous["unidentified_nonterminal"] - current["unidentified_nonterminal"])
    if not removed and not unidentified_shortfall:
        return
    rejection = _drain_marker_rejection(new_state, previous_state, removed=removed, unidentified_shortfall=unidentified_shortfall)
    if rejection is None:
        return
    survivors = previous["nonterminal_ids"] & current["ids"]
    mode = "mass replacement" if removed and not survivors else "disappearance"
    detail = ", ".join(removed[:REJECTION_ID_SAMPLE]) or "unidentified rows"
    if len(removed) > REJECTION_ID_SAMPLE:
        detail += f", ... (+{len(removed) - REJECTION_ID_SAMPLE} more)"
    raise TaskStateStoreError(
        f"{NONTERMINAL_DROP_REJECTION}: {mode} would remove {len(removed) + unidentified_shortfall} nonterminal task(s) [{detail}] leaving {len(survivors)} of {len(previous['nonterminal_ids']) + previous['unidentified_nonterminal']} live tasks; {rejection}"
    )


def _archive_identity(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "archive_file": manifest["archive_file"],
        "byte_size": manifest["byte_size"],
        "final_sequence": manifest["final_sequence"],
        "journal_sha256": manifest["journal_sha256"],
        "projected_state_sha256": manifest["projected_state_sha256"],
        "manifest_sha256": manifest["manifest_sha256"],
    }


def _validate_archive_identity(identity: Any) -> dict[str, Any] | None:
    if identity is None:
        return None
    required = {"archive_file", "byte_size", "final_sequence", "journal_sha256", "projected_state_sha256", "manifest_sha256"}
    if not isinstance(identity, dict) or set(identity) != required:
        raise TaskStateStoreError("task-state archive identity schema mismatch")
    if not isinstance(identity["archive_file"], str) or "/" in identity["archive_file"] or not identity["archive_file"]:
        raise TaskStateStoreError("task-state archive identity has invalid archive file")
    if not isinstance(identity["byte_size"], int) or identity["byte_size"] < 0:
        raise TaskStateStoreError("task-state archive identity has invalid byte size")
    if not isinstance(identity["final_sequence"], int) or identity["final_sequence"] < 0:
        raise TaskStateStoreError("task-state archive identity has invalid final sequence")
    for field in ("journal_sha256", "projected_state_sha256", "manifest_sha256"):
        _validate_sha256(identity[field], label=f"task-state archive identity {field}")
    return copy.deepcopy(identity)


def _make_head(*, sequence: int, state: dict[str, Any], last_event_sha256: str | None, delta_offset: int, archive_identity: dict[str, Any] | None, updated_at: str | None = None) -> dict[str, Any]:
    if sequence < 0 or delta_offset < 0 or not isinstance(state, dict):
        raise TaskStateStoreError("invalid task-state head input")
    head = {
        "version": HEAD_VERSION,
        "type": HEAD_TYPE,
        "sequence": sequence,
        "updated_at": updated_at or utc_now(),
        "state": copy.deepcopy(state),
        "state_sha256": sha256_json(state),
        "last_event_sha256": last_event_sha256,
        "delta_offset": delta_offset,
        "archive_identity": _validate_archive_identity(archive_identity),
    }
    head["head_sha256"] = sha256_json(_head_digest_payload(head))
    return head


def _validate_head(head: Any) -> dict[str, Any]:
    required = {"version", "type", "sequence", "updated_at", "state", "state_sha256", "last_event_sha256", "delta_offset", "archive_identity", "head_sha256"}
    if not isinstance(head, dict) or set(head) != required:
        raise TaskStateStoreError("task-state head schema mismatch")
    if head.get("version") != HEAD_VERSION or head.get("type") != HEAD_TYPE:
        raise TaskStateStoreError("task-state head has unsupported version/type")
    if not isinstance(head["sequence"], int) or head["sequence"] < 0:
        raise TaskStateStoreError("task-state head has invalid sequence")
    if not isinstance(head["delta_offset"], int) or head["delta_offset"] < 0:
        raise TaskStateStoreError("task-state head has invalid delta offset")
    if not isinstance(head["state"], dict) or head["state_sha256"] != sha256_json(head["state"]):
        raise TaskStateStoreError("task-state head state digest mismatch")
    if head["sequence"] == 0 and head["last_event_sha256"] is not None:
        raise TaskStateStoreError("empty task-state head cannot name a last event")
    if head["sequence"] and not isinstance(head["last_event_sha256"], str):
        raise TaskStateStoreError("task-state head lacks last event digest")
    if head["last_event_sha256"] is not None:
        _validate_sha256(head["last_event_sha256"], label="task-state head last event digest")
    if not isinstance(head["updated_at"], str) or not head["updated_at"].strip():
        raise TaskStateStoreError("task-state head lacks update time")
    _validate_archive_identity(head["archive_identity"])
    if head["head_sha256"] != sha256_json(_head_digest_payload(head)):
        raise TaskStateStoreError("task-state head digest mismatch")
    return copy.deepcopy(head)


def _read_head(event_path: Path, *, missing_ok: bool = False) -> dict[str, Any] | None:
    payload = _read_regular_bytes(_head_path(event_path), label="head", missing_ok=missing_ok)
    if not payload and missing_ok and not _head_path(event_path).exists():
        return None
    try:
        return _validate_head(json.loads(payload.decode("utf-8")))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TaskStateStoreError("task-state head is not valid JSON") from exc


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, stat.S_IRUSR | stat.S_IWUSR)
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise TaskStateStoreError("short task-state atomic write")
            offset += written
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _write_head_cas(event_path: Path, head: dict[str, Any], *, expected_head_sha256: str | None) -> dict[str, Any]:
    """Replace the compact head only if the writer's generation is still current."""

    target = _head_path(event_path)
    current = _read_head(event_path, missing_ok=True)
    actual = current["head_sha256"] if current is not None else None
    if actual != expected_head_sha256:
        raise TaskStateStoreError("stale task-state head CAS")
    checked = _validate_head(head)
    _atomic_write_bytes(target, canonical_json_bytes(checked) + b"\n")
    return checked


def _validate_transition_event(event: Any, *, expected_sequence: int, previous_event_sha256: str | None, previous_state: dict[str, Any], archive_identity: dict[str, Any] | None) -> tuple[dict[str, Any], dict[str, Any]]:
    required = {"version", "type", "event_id", "event_sha256", "sequence", "committed_at", "source", "previous_event_sha256", "base_state_sha256", "state_sha256", "delta", "archive_identity"}
    if not isinstance(event, dict) or set(event) != required:
        raise TaskStateStoreError("task-state transition schema mismatch")
    if event.get("version") != EVENT_VERSION or event.get("type") != EVENT_TYPE_STATE_COMMITTED:
        raise TaskStateStoreError("task-state transition has unsupported version/type")
    if event.get("sequence") != expected_sequence:
        raise TaskStateStoreError(f"task-state sequence conflict: expected {expected_sequence}, got {event.get('sequence')!r}")
    if event.get("previous_event_sha256") != previous_event_sha256:
        raise TaskStateStoreError("task-state transition previous hash mismatch")
    if event.get("archive_identity") != archive_identity:
        raise TaskStateStoreError("task-state transition archive identity mismatch")
    if event.get("base_state_sha256") != sha256_json(previous_state):
        raise TaskStateStoreError("task-state transition base state digest mismatch")
    if not isinstance(event.get("committed_at"), str) or not event["committed_at"].strip() or not isinstance(event.get("source"), str) or not event["source"].strip():
        raise TaskStateStoreError("task-state transition lacks commit provenance")
    _validate_sha256(event.get("state_sha256"), label="task-state transition state digest")
    event_sha256 = sha256_json(_event_digest_payload(event))
    if event.get("event_sha256") != event_sha256 or event.get("event_id") != f"task-state-{event_sha256}":
        raise TaskStateStoreError("task-state transition event digest mismatch")
    state = apply_delta(previous_state, event["delta"])
    if event["state_sha256"] != sha256_json(state):
        raise TaskStateStoreError("task-state transition resulting state digest mismatch")
    validate_state_transition(state, previous_state)
    return copy.deepcopy(event), state


def _make_transition(previous_state: dict[str, Any], state: dict[str, Any], *, sequence: int, previous_event_sha256: str | None, archive_identity: dict[str, Any] | None, source: str, committed_at: str | None) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(state, dict):
        raise TaskStateStoreError("task-state commit must contain an object state")
    event: dict[str, Any] = {
        "version": EVENT_VERSION,
        "type": EVENT_TYPE_STATE_COMMITTED,
        "sequence": sequence,
        "committed_at": committed_at or utc_now(),
        "source": str(source or "unknown").strip() or "unknown",
        "previous_event_sha256": previous_event_sha256,
        "base_state_sha256": sha256_json(previous_state),
        "state_sha256": sha256_json(state),
        "delta": _state_delta(previous_state, state),
        "archive_identity": copy.deepcopy(archive_identity),
    }
    event["event_sha256"] = sha256_json(event)
    event["event_id"] = f"task-state-{event['event_sha256']}"
    return _validate_transition_event(event, expected_sequence=sequence, previous_event_sha256=previous_event_sha256, previous_state=previous_state, archive_identity=archive_identity)


def _append_transition(event_path: Path, event: dict[str, Any]) -> int:
    payload = canonical_json_bytes(event) + b"\n"
    descriptor = os.open(event_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise TaskStateStoreError(f"task-state transition log must be a regular file: {event_path}")
        os.fchmod(descriptor, stat.S_IRUSR | stat.S_IWUSR)
        before = os.lseek(descriptor, 0, os.SEEK_END)
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise TaskStateStoreError("short task-state transition write")
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    _fsync_directory(event_path.parent)
    return before + len(payload)


def _read_transition_tail(event_path: Path, offset: int) -> tuple[bytes, int]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(event_path, flags)
    except FileNotFoundError:
        if offset == 0:
            return b"", 0
        raise TaskStateStoreError("task-state transition log is truncated before head offset") from None
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise TaskStateStoreError(f"task-state transition log must be a regular file: {event_path}")
        if info.st_size < offset:
            raise TaskStateStoreError("task-state transition log is truncated before head offset")
        remaining = info.st_size - offset
        chunks: list[bytes] = []
        position = offset
        while remaining:
            chunk = os.pread(descriptor, min(1024 * 1024, remaining), position)
            if not chunk:
                raise TaskStateStoreError("task-state transition tail read was short")
            chunks.append(chunk)
            position += len(chunk)
            remaining -= len(chunk)
        return b"".join(chunks), int(info.st_size)
    finally:
        os.close(descriptor)


def _replay_transition_bytes(payload: bytes, *, sequence: int, previous_event_sha256: str | None, state: dict[str, Any], archive_identity: dict[str, Any] | None) -> tuple[int, str | None, dict[str, Any], list[tuple[dict[str, Any], dict[str, Any]]]]:
    if payload and not payload.endswith(b"\n"):
        raise TaskStateStoreError("corrupted task-state transition tail: incomplete record")
    events: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for raw_line in payload.splitlines():
        if not raw_line.strip():
            raise TaskStateStoreError("corrupted task-state transition tail: blank record")
        try:
            raw_event = json.loads(raw_line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TaskStateStoreError("corrupted task-state transition tail: invalid JSON") from exc
        event, state = _validate_transition_event(raw_event, expected_sequence=sequence + 1, previous_event_sha256=previous_event_sha256, previous_state=state, archive_identity=archive_identity)
        sequence += 1
        previous_event_sha256 = event["event_sha256"]
        events.append((event, state))
    return sequence, previous_event_sha256, state, events


def _validate_archive_genesis_binding(
    events: list[tuple[dict[str, Any], dict[str, Any]]],
    archive_identity: dict[str, Any] | None,
) -> None:
    """Ensure the V2 genesis projection is the state frozen in the V1 archive.

    This validation is deliberately reserved for interrupted-migration resume
    and offline full-chain verification.  A normal head/tail read must not
    inspect the frozen archive or turn its integrity checks into hot-path work.
    """

    if archive_identity is None:
        return
    if not events or events[0][0].get("sequence") != 1:
        raise TaskStateStoreError("task-state V2 archive binding lacks a genesis transition")
    genesis, projected_state = events[0]
    expected_state_sha256 = archive_identity["projected_state_sha256"]
    if (
        genesis["state_sha256"] != expected_state_sha256
        or sha256_json(projected_state) != expected_state_sha256
    ):
        raise TaskStateStoreError(
            "task-state V2 genesis state does not match archive projected-state binding"
        )


def _virtual_empty_head() -> dict[str, Any]:
    return _make_head(sequence=0, state={}, last_event_sha256=None, delta_offset=0, archive_identity=None, updated_at="1970-01-01T00:00:00Z")


def _load_snapshot_unlocked(event_path: Path, *, allow_empty: bool = False) -> dict[str, Any]:
    physical_head = _read_head(event_path, missing_ok=True)
    if physical_head is None:
        payload, size = _read_transition_tail(event_path, 0)
        if payload:
            raise TaskStateStoreError("task-state V2 head is missing; run migrate_task_state_store_v2.py, not a V1 fallback")
        if not allow_empty:
            raise TaskStateStoreError("task-state V2 head is missing; store has not been initialized")
        head = _virtual_empty_head()
        return {
            "head": head,
            "physical_head_sha256": None,
            "last_event": None,
            "tail_event_count": 0,
            "byte_size": size,
        }
    tail, byte_size = _read_transition_tail(event_path, physical_head["delta_offset"])
    sequence, last_sha, state, events = _replay_transition_bytes(tail, sequence=physical_head["sequence"], previous_event_sha256=physical_head["last_event_sha256"], state=physical_head["state"], archive_identity=physical_head["archive_identity"])
    if events:
        head = _make_head(sequence=sequence, state=state, last_event_sha256=last_sha, delta_offset=byte_size, archive_identity=physical_head["archive_identity"])
        last_event = events[-1][0]
    else:
        head = physical_head
        last_event = None
    return {
        "head": head,
        "physical_head_sha256": physical_head["head_sha256"],
        "last_event": last_event,
        "tail_event_count": len(events),
        "byte_size": byte_size,
    }


def _public_event(event: dict[str, Any] | None, state: dict[str, Any]) -> dict[str, Any] | None:
    if event is None:
        return None
    public = copy.deepcopy(event)
    # Read-only compatibility for existing audit callers.  This field is never
    # persisted in V2 transition records and is not part of their digest.
    public["state"] = copy.deepcopy(state)
    return public


def _public_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    head = snapshot["head"]
    return {
        "event_count": int(head["sequence"]),
        "last_event": _public_event(snapshot.get("last_event"), head["state"]),
        "last_event_id": (f"task-state-{head['last_event_sha256']}" if head["last_event_sha256"] else None),
        "last_event_sha256": head["last_event_sha256"],
        "state": copy.deepcopy(head["state"]),
        "state_sha256": head["state_sha256"],
        "byte_size": int(snapshot["byte_size"]),
        "head_sequence": int(head["sequence"]),
        "head_sha256": head["head_sha256"],
        "tail_event_count": int(snapshot["tail_event_count"]),
        "recovered_tail": bool(snapshot["tail_event_count"]),
        "archive_identity": copy.deepcopy(head["archive_identity"]),
    }


def load_snapshot(path: str | Path, *, observational: bool = False) -> dict[str, Any]:
    """Read the current V2 head plus only the bounded tail after it.

    V2 has no replay checkpoint, mmap, prefix hash, or V1 fallback.
    Observational reads cannot create a parent or lock sidecar; default reads
    may provision an empty fresh store for shadow/bootstrap flow. A nonempty
    no-head log always fails rather than being treated as V1.
    """

    event_path = (
        _require_existing_parent(Path(path))
        if observational
        else _prepare_parent(Path(path))
    )
    with _store_lock(event_path, shared=True, observational=observational):
        return _public_snapshot(
            _load_snapshot_unlocked(event_path, allow_empty=not observational)
        )


def _append_state_commit_unlocked(event_path: Path, state: dict[str, Any], *, source: str, committed_at: str | None, snapshot: dict[str, Any], expected_sequence: int | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    head = snapshot["head"]
    if expected_sequence is not None and int(expected_sequence) != int(head["sequence"]):
        raise TaskStateStoreError("stale task-state head CAS")
    if not isinstance(state, dict):
        raise TaskStateStoreError("task-state commit must contain an object state")
    if head["sequence"] and sha256_json(state) == head["state_sha256"]:
        return _public_event(snapshot.get("last_event"), head["state"]) or {}, snapshot
    event, resulting_state = _make_transition(head["state"], state, sequence=head["sequence"] + 1, previous_event_sha256=head["last_event_sha256"], archive_identity=head["archive_identity"], source=source, committed_at=committed_at)
    next_offset = _append_transition(event_path, event)
    next_head = _make_head(sequence=event["sequence"], state=resulting_state, last_event_sha256=event["event_sha256"], delta_offset=next_offset, archive_identity=head["archive_identity"], updated_at=event["committed_at"])
    written_head = _write_head_cas(event_path, next_head, expected_head_sha256=snapshot["physical_head_sha256"])
    next_snapshot = {
        "head": written_head,
        "physical_head_sha256": written_head["head_sha256"],
        "last_event": event,
        "tail_event_count": 0,
        "byte_size": next_offset,
    }
    return _public_event(event, resulting_state) or {}, next_snapshot


def append_state_commit(path: str | Path, state: dict[str, Any], *, source: str, committed_at: str | None = None, expected_sequence: int | None = None) -> dict[str, Any]:
    event_path = _prepare_parent(Path(path))
    with _store_lock(event_path, shared=False):
        snapshot = _load_snapshot_unlocked(event_path, allow_empty=True)
        event, _next = _append_state_commit_unlocked(event_path, state, source=source, committed_at=committed_at, snapshot=snapshot, expected_sequence=expected_sequence)
        return copy.deepcopy(event)


class SnapshotTransaction:
    """One writer-locked V2 transaction with a single compact-head read."""

    def __init__(self, event_path: Path, snapshot: dict[str, Any]) -> None:
        self._event_path = event_path
        self._snapshot = snapshot

    def load_snapshot(self) -> dict[str, Any]:
        return _public_snapshot(self._snapshot)

    def append_state_commit(self, state: dict[str, Any], *, source: str, committed_at: str | None = None, expected_sequence: int | None = None) -> dict[str, Any]:
        event, self._snapshot = _append_state_commit_unlocked(self._event_path, state, source=source, committed_at=committed_at, snapshot=self._snapshot, expected_sequence=expected_sequence)
        return copy.deepcopy(event)


@contextmanager
def snapshot_transaction(path: str | Path) -> Iterator[SnapshotTransaction]:
    event_path = _prepare_parent(Path(path))
    with _store_lock(event_path, shared=False):
        yield SnapshotTransaction(event_path, _load_snapshot_unlocked(event_path, allow_empty=True))


def _load_all_v2_events_unlocked(event_path: Path) -> tuple[list[tuple[dict[str, Any], dict[str, Any]]], dict[str, Any]]:
    head = _read_head(event_path, missing_ok=False)
    assert head is not None
    payload, size = _read_transition_tail(event_path, 0)
    sequence, last_sha, state, events = _replay_transition_bytes(payload, sequence=0, previous_event_sha256=None, state={}, archive_identity=head["archive_identity"])
    _validate_archive_genesis_binding(events, head["archive_identity"])
    if head["sequence"] > sequence:
        raise TaskStateStoreError("task-state full-chain verification disagrees with current head")
    if head["sequence"]:
        physical_event, physical_state = events[head["sequence"] - 1]
        physical_last_sha = physical_event["event_sha256"]
    else:
        physical_state = {}
        physical_last_sha = None
    physical_offset = sum(
        len(raw_line) for raw_line in payload.splitlines(keepends=True)[: head["sequence"]]
    )
    if (
        physical_last_sha != head["last_event_sha256"]
        or physical_state != head["state"]
        or physical_offset != head["delta_offset"]
    ):
        raise TaskStateStoreError("task-state full-chain verification disagrees with current head")
    return events, head


def load_events(path: str | Path) -> list[dict[str, Any]]:
    """Offline/audit compatibility reader for V2 transitions.

    It reconstructs materialized states for legacy callers, but is deliberately
    never used by normal scheduling or mutation paths.
    """

    event_path = _require_existing_parent(Path(path))
    with _store_lock(event_path, shared=True, observational=True):
        events, _head = _load_all_v2_events_unlocked(event_path)
    return [_public_event(event, state) or {} for event, state in events]


def project_latest_state(events: Iterable[dict[str, Any]]) -> dict[str, Any]:
    latest: dict[str, Any] = {}
    for event in events:
        state = event.get("state") if isinstance(event, dict) else None
        if not isinstance(state, dict):
            raise TaskStateStoreError("V2 audit event lacks reconstructed state")
        latest = state
    return copy.deepcopy(latest)


def verify_snapshot(snapshot: dict[str, Any], expected_state: dict[str, Any]) -> dict[str, Any]:
    projected_sha256 = str(snapshot["state_sha256"])
    expected_sha256 = sha256_json(expected_state)
    census = _task_census(snapshot["state"])
    return {
        "ok": bool(snapshot["event_count"]) and projected_sha256 == expected_sha256,
        "event_count": int(snapshot["event_count"]),
        "last_event_id": snapshot["last_event_id"],
        "projected_state_sha256": projected_sha256,
        "expected_state_sha256": expected_sha256,
        "nonterminal_task_count": len(census["nonterminal_ids"]) + census["unidentified_nonterminal"],
        "tail_event_count": int(snapshot.get("tail_event_count") or 0),
    }


def verify_projection(path: str | Path, expected_state: dict[str, Any]) -> dict[str, Any]:
    return verify_snapshot(load_snapshot(path), expected_state)


def _validate_v1_event(event: Any, *, expected_sequence: int, previous_sha256: str | None) -> tuple[str, dict[str, Any]]:
    required = {"version", "type", "event_id", "event_sha256", "sequence", "committed_at", "source", "previous_event_sha256", "state_sha256", "state"}
    if not isinstance(event, dict) or set(event) != required:
        raise TaskStateStoreError("legacy task-state archive event schema mismatch")
    if event.get("version") != 1 or event.get("type") != "task_state_committed":
        raise TaskStateStoreError("legacy task-state archive has unsupported version/type")
    if event.get("sequence") != expected_sequence or event.get("previous_event_sha256") != previous_sha256:
        raise TaskStateStoreError("legacy task-state archive chain mismatch")
    state = event.get("state")
    if not isinstance(state, dict) or event.get("state_sha256") != sha256_json(state):
        raise TaskStateStoreError("legacy task-state archive state digest mismatch")
    digest = sha256_json(_event_digest_payload(event))
    if event.get("event_sha256") != digest or event.get("event_id") != f"task-state-{digest}":
        raise TaskStateStoreError("legacy task-state archive event digest mismatch")
    # The archive reader retains only the final projected state.  Returning a
    # digest rather than a copied full event keeps validation bounded by one
    # record plus that projection rather than by the complete V1 journal.
    return str(event["event_sha256"]), state


def _read_legacy_journal(path: Path) -> tuple[dict[str, Any], int, int, str]:
    """Validate a V1 JSONL archive without aggregating its complete payload.

    This deliberately does not call ``_read_regular_bytes``: migration can
    process multi-gigabyte archives, so retaining either the whole payload or
    a ``splitlines`` copy would make the archive size, rather than the largest
    record and current projected state, the migration memory bound.
    """

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        raise TaskStateStoreError(f"task-state legacy archive is missing: {path}") from None
    except OSError as exc:
        raise TaskStateStoreError(f"cannot open task-state legacy archive: {path}") from exc

    state: dict[str, Any] = {}
    previous: str | None = None
    sequence = 0
    byte_size = 0
    journal_digest = hashlib.sha256()
    pending = bytearray()

    def consume_record(raw_line: bytearray | bytes) -> None:
        nonlocal previous, sequence, state
        if not raw_line.strip():
            raise TaskStateStoreError("legacy task-state archive has blank record")
        # Drop the superseded V1 full-board projection before decoding the next
        # record.  The V1 chain validates through hashes, not a prior state
        # diff, so it is not needed while parsing the next event.
        state = {}
        try:
            event = json.loads(raw_line)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TaskStateStoreError("legacy task-state archive has invalid JSON") from exc
        previous, state = _validate_v1_event(
            event,
            expected_sequence=sequence + 1,
            previous_sha256=previous,
        )
        sequence += 1

    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise TaskStateStoreError(f"task-state legacy archive must be a regular file: {path}")
        while True:
            try:
                chunk = os.read(descriptor, LEGACY_ARCHIVE_READ_CHUNK_BYTES)
            except OSError as exc:
                raise TaskStateStoreError(f"cannot read task-state legacy archive: {path}") from exc
            if not chunk:
                break
            byte_size += len(chunk)
            journal_digest.update(chunk)
            records = chunk.split(b"\n")
            if len(records) == 1:
                pending.extend(records[0])
                continue
            pending.extend(records[0])
            consume_record(pending)
            pending.clear()
            for raw_line in records[1:-1]:
                consume_record(raw_line)
            pending.extend(records[-1])
        if pending:
            raise TaskStateStoreError("legacy task-state archive has incomplete record")
    finally:
        os.close(descriptor)
    return state, sequence, byte_size, journal_digest.hexdigest()


def _make_archive_manifest(event_path: Path, *, state: dict[str, Any], final_sequence: int, byte_size: int, journal_sha256: str) -> dict[str, Any]:
    manifest = {
        "version": ARCHIVE_VERSION,
        "type": ARCHIVE_TYPE,
        "archive_file": _archive_path(event_path).name,
        "byte_size": byte_size,
        "final_sequence": final_sequence,
        "journal_sha256": journal_sha256,
        "projected_state_sha256": sha256_json(state),
    }
    manifest["manifest_sha256"] = sha256_json(_archive_digest_payload(manifest))
    return manifest


def _read_archive_manifest(event_path: Path) -> dict[str, Any]:
    payload = _read_regular_bytes(_archive_manifest_path(event_path), label="archive manifest")
    try:
        manifest = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TaskStateStoreError("task-state archive manifest is not valid JSON") from exc
    required = {"version", "type", "archive_file", "byte_size", "final_sequence", "journal_sha256", "projected_state_sha256", "manifest_sha256"}
    if not isinstance(manifest, dict) or set(manifest) != required:
        raise TaskStateStoreError("task-state archive manifest schema mismatch")
    if manifest.get("version") != ARCHIVE_VERSION or manifest.get("type") != ARCHIVE_TYPE:
        raise TaskStateStoreError("task-state archive manifest has unsupported version/type")
    if manifest.get("archive_file") != _archive_path(event_path).name:
        raise TaskStateStoreError("task-state archive manifest names an unexpected archive")
    _validate_archive_identity(_archive_identity(manifest))
    if manifest.get("manifest_sha256") != sha256_json(_archive_digest_payload(manifest)):
        raise TaskStateStoreError("task-state archive manifest digest mismatch")
    return copy.deepcopy(manifest)


def _write_archive_manifest(event_path: Path, manifest: dict[str, Any]) -> None:
    _atomic_write_bytes(_archive_manifest_path(event_path), canonical_json_bytes(manifest) + b"\n")


def _validate_archive_manifest_against_bytes(
    manifest: dict[str, Any],
    *,
    state: dict[str, Any],
    final_sequence: int,
    byte_size: int,
    journal_sha256: str,
) -> None:
    """Fail closed when a resumed migration cannot bind its frozen archive."""

    expected = {
        "byte_size": byte_size,
        "final_sequence": final_sequence,
        "journal_sha256": journal_sha256,
        "projected_state_sha256": sha256_json(state),
    }
    mismatches = [field for field, value in expected.items() if manifest.get(field) != value]
    if mismatches:
        raise TaskStateStoreError(
            "task-state archive manifest does not match frozen archive: "
            + ", ".join(mismatches)
        )


def _verify_archive(event_path: Path, identity: dict[str, Any] | None) -> dict[str, Any] | None:
    if identity is None:
        return None
    manifest = _read_archive_manifest(event_path)
    if _archive_identity(manifest) != identity:
        raise TaskStateStoreError("task-state archive manifest does not match V2 genesis binding")
    state, sequence, byte_size, journal_sha256 = _read_legacy_journal(_archive_path(event_path))
    _validate_archive_manifest_against_bytes(
        manifest,
        state=state,
        final_sequence=sequence,
        byte_size=byte_size,
        journal_sha256=journal_sha256,
    )
    return manifest


def verify_full_chain(path: str | Path) -> dict[str, Any]:
    """Offline full-chain verification; never call this from a scheduler tick."""

    event_path = _require_existing_parent(Path(path))
    with _store_lock(event_path, shared=True, observational=True):
        events, head = _load_all_v2_events_unlocked(event_path)
        manifest = _verify_archive(event_path, head["archive_identity"])
    recovered_state = events[-1][1] if events else {}
    tail_event_count = len(events) - int(head["sequence"])
    return {
        "ok": True,
        "event_count": len(events),
        "state_sha256": sha256_json(recovered_state),
        "head_sequence": int(head["sequence"]),
        "tail_event_count": tail_event_count,
        "archive_verified": manifest is not None,
        "archive_final_sequence": manifest["final_sequence"] if manifest else None,
    }


def migrate_legacy_journal(path: str | Path, *, dry_run: bool = False, source: str = "task-state-store-v2-migration") -> dict[str, Any]:
    """Freeze a V1 full-state journal and initialize the V2 store in place.

    The configured runtime path stays stable: the old bytes move once to the
    sibling archive, while the original path becomes the V2 transition log.
    Interrupted migration is resumable only from the preserved archive; no V1
    runtime read fallback is ever enabled.
    """

    event_path = _require_existing_parent(Path(path)) if dry_run else _prepare_parent(Path(path))
    # A dry run is advisory and intentionally creates no lock or sidecar.  The
    # mutating migration still owns the one exclusive task-state lock.
    lock = nullcontext() if dry_run else _store_lock(event_path, shared=False)
    with lock:
        existing_head = _read_head(event_path, missing_ok=True)
        if existing_head is not None:
            if dry_run:
                return {"status": "already_v2", "event_log": str(event_path), "sequence": existing_head["sequence"]}
            return {"status": "already_v2", "event_log": str(event_path), "sequence": existing_head["sequence"]}
        archive = _archive_path(event_path)
        manifest_path = _archive_manifest_path(event_path)
        if archive.exists():
            state, final_sequence, byte_size, journal_sha256 = _read_legacy_journal(archive)
            if manifest_path.exists():
                manifest = _read_archive_manifest(event_path)
                _validate_archive_manifest_against_bytes(
                    manifest,
                    state=state,
                    final_sequence=final_sequence,
                    byte_size=byte_size,
                    journal_sha256=journal_sha256,
                )
            else:
                manifest = _make_archive_manifest(event_path, state=state, final_sequence=final_sequence, byte_size=byte_size, journal_sha256=journal_sha256)
        else:
            if manifest_path.exists():
                raise TaskStateStoreError("task-state archive manifest exists without a frozen archive")
            state, final_sequence, byte_size, journal_sha256 = _read_legacy_journal(event_path)
            manifest = _make_archive_manifest(event_path, state=state, final_sequence=final_sequence, byte_size=byte_size, journal_sha256=journal_sha256)
        if dry_run:
            return {"status": "planned", "event_log": str(event_path), "archive": str(archive), "archive_final_sequence": final_sequence, "archive_byte_size": byte_size, "projected_state_sha256": sha256_json(state)}
        if not archive.exists():
            os.replace(event_path, archive)
        # A crash can happen after the rename but before chmod.  Every resume
        # reasserts the immutable archive mode before it writes a manifest,
        # genesis, or replacement head.
        os.chmod(archive, stat.S_IRUSR)
        _fsync_directory(event_path.parent)
        if not manifest_path.exists():
            _write_archive_manifest(event_path, manifest)
        identity = _archive_identity(manifest)
        payload, size = _read_transition_tail(event_path, 0)
        if payload:
            sequence, last_sha, recovered_state, events = _replay_transition_bytes(payload, sequence=0, previous_event_sha256=None, state={}, archive_identity=identity)
            _validate_archive_genesis_binding(events, identity)
            head = _make_head(sequence=sequence, state=recovered_state, last_event_sha256=last_sha, delta_offset=size, archive_identity=identity)
            _write_head_cas(event_path, head, expected_head_sha256=None)
            return {"status": "recovered_interrupted_migration", "event_log": str(event_path), "archive": str(archive), "sequence": sequence}
        event, resulting_state = _make_transition({}, state, sequence=1, previous_event_sha256=None, archive_identity=identity, source=source, committed_at=utc_now())
        offset = _append_transition(event_path, event)
        head = _make_head(sequence=1, state=resulting_state, last_event_sha256=event["event_sha256"], delta_offset=offset, archive_identity=identity, updated_at=event["committed_at"])
        _write_head_cas(event_path, head, expected_head_sha256=None)
        return {"status": "migrated", "event_log": str(event_path), "archive": str(archive), "archive_final_sequence": final_sequence, "archive_byte_size": byte_size, "archive_journal_sha256": journal_sha256, "sequence": 1}
