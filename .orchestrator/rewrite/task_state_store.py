"""Durable append-only task-state journal for Supervisor Rewrite Phase 6.

The migration began in shadow mode and now supports authoritative reads and
writes. Each event carries the complete state, a state digest, and a
previous-event hash. In authoritative mode the journal is committed before the
derived ``ai-status.json`` projection, so a crash cannot promote an unjournaled
board mutation and projection drift is repaired in the journal-to-file direction.
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

# A task leaves the live board only through one of these statuses. Anything else
# -- including a blank, unknown, or unreadable status -- is treated as live work.
TERMINAL_TASK_STATUSES = frozenset({"done", "supersede", "superseded", "cancelled", "canceled"})
# Removing a live task is legal only when the commit carries this audited marker.
DRAIN_MARKER_KEY = "task_state_drain"
DRAIN_MARKER_AUDIT_FIELDS = ("reason", "actor", "approved_at")
DRAIN_MARKER_TIMESTAMP_FIELD = "approved_at"
NONTERMINAL_DROP_REJECTION = "task-state nonterminal drop rejected"
REJECTION_ID_SAMPLE = 5


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
    # A relative path would resolve against the caller's working directory, so an
    # inherited configuration could silently mint a private journal inside a task
    # worktree instead of binding the provisioned live event log.
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


def _task_rows(state: Any) -> tuple[list[Any], bool]:
    """Return the task rows of a state plus whether the container was well formed."""

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
    # Fail closed: an unknown, blank, or unreadable status is treated as live
    # work, so only an explicitly terminal row may leave the board silently.
    if not isinstance(task, dict):
        return False
    return str(task.get("status") or "").strip().lower() in TERMINAL_TASK_STATUSES


def _task_census(state: Any) -> dict[str, Any]:
    """Index a state by task identity so a transition can be compared by id."""

    rows, container_ok = _task_rows(state)
    terminal_by_id: dict[str, bool] = {}
    unidentified_nonterminal = 0
    for task in rows:
        identity = _task_identity(task)
        terminal = _is_terminal_task(task)
        if identity is None:
            # A row without a usable id cannot be tracked across commits; count
            # it so the population can still be compared.
            if not terminal:
                unidentified_nonterminal += 1
            continue
        # A duplicated id keeps the most protective reading of its status.
        terminal_by_id[identity] = terminal_by_id.get(identity, True) and terminal
    if not container_ok:
        # An unreadable task container hides an unknown amount of live work.
        unidentified_nonterminal += 1
    return {
        "ids": set(terminal_by_id),
        "nonterminal_ids": {
            identity for identity, terminal in terminal_by_id.items() if not terminal
        },
        "unidentified_nonterminal": unidentified_nonterminal,
    }


def nonterminal_task_ids(state: Any) -> set[str]:
    """Identities of tasks that are still live in ``state``."""

    return set(_task_census(state)["nonterminal_ids"])


def _parse_audit_timestamp(value: str) -> datetime | None:
    """Parse an audit timestamp, requiring an explicit UTC offset."""

    text = value.strip()
    if text[-1:] in {"z", "Z"}:
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    # A naive timestamp cannot be ordered against the commit clock, so an
    # unauditable "approved at 00:00" claim must not license a drop.
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _drain_marker_audit_rejection(marker: dict[str, Any]) -> str | None:
    """Return ``None`` when the marker's audit fields are usable as evidence."""

    # A non-string audit value (number, bool, object) is not an auditable
    # reason/actor/timestamp, so it is refused exactly like a blank one.
    missing = [
        field
        for field in DRAIN_MARKER_AUDIT_FIELDS
        if not isinstance(marker.get(field), str) or not marker[field].strip()
    ]
    if missing:
        return f"{DRAIN_MARKER_KEY} lacks audit fields {missing}"
    raw_approved_at = marker[DRAIN_MARKER_TIMESTAMP_FIELD]
    approved_at = _parse_audit_timestamp(raw_approved_at)
    if approved_at is None:
        return (
            f"{DRAIN_MARKER_KEY}.{DRAIN_MARKER_TIMESTAMP_FIELD} must be a timezone-aware "
            f"ISO 8601 timestamp: {raw_approved_at!r}"
        )
    if approved_at > datetime.now(timezone.utc):
        # An approval dated in the future was never granted by anyone.
        return (
            f"{DRAIN_MARKER_KEY}.{DRAIN_MARKER_TIMESTAMP_FIELD} is in the future: "
            f"{raw_approved_at!r}"
        )
    return None


def _drain_marker_ids(marker: dict[str, Any]) -> tuple[set[str] | None, str | None]:
    """Return the authorized id set, or the reason the id list is unusable."""

    raw_ids = marker.get("task_ids")
    if not isinstance(raw_ids, list) or not all(
        isinstance(item, str) and item.strip() for item in raw_ids
    ):
        return None, f"{DRAIN_MARKER_KEY}.task_ids must list the removed task ids"
    authorized = [item.strip() for item in raw_ids]
    duplicates = sorted({item for item in authorized if authorized.count(item) > 1})
    if duplicates:
        # A repeated id inflates the marker's apparent coverage without adding
        # evidence, so the list must be a clean set of the dropped identities.
        return None, f"{DRAIN_MARKER_KEY}.task_ids repeats task ids: {duplicates}"
    return set(authorized), None


def _drain_marker_rejection(
    new_state: dict[str, Any],
    previous_state: Any,
    *,
    removed: list[str],
    unidentified_shortfall: int,
) -> str | None:
    """Return ``None`` when an explicit audited drain marker covers the removal."""

    marker = new_state.get(DRAIN_MARKER_KEY)
    if marker is None:
        return f"no explicit audited {DRAIN_MARKER_KEY} marker was supplied"
    if not isinstance(marker, dict):
        return f"{DRAIN_MARKER_KEY} must be an object"
    audit_rejection = _drain_marker_audit_rejection(marker)
    if audit_rejection is not None:
        return audit_rejection
    if isinstance(previous_state, dict) and previous_state.get(DRAIN_MARKER_KEY) == marker:
        # A marker carried forward unchanged would disable the guard forever.
        return f"{DRAIN_MARKER_KEY} is an unchanged copy of the previous commit"
    authorized, id_rejection = _drain_marker_ids(marker)
    if authorized is None:
        return id_rejection
    still_present = sorted(authorized & _task_census(new_state)["ids"])
    if still_present:
        # The marker must describe this drain only, not pre-authorize future ones.
        return f"{DRAIN_MARKER_KEY} names tasks that are still on the board: {still_present}"
    uncovered = sorted(set(removed) - authorized)
    if uncovered:
        return f"{DRAIN_MARKER_KEY} does not cover removed tasks: {uncovered}"
    unrelated = sorted(authorized - set(removed))
    if unrelated:
        # The id set must equal this commit's live removals: an id that was never
        # live here is a padded marker, not evidence for the drop being made.
        return (
            f"{DRAIN_MARKER_KEY} names tasks that were not live removals in this "
            f"commit: {unrelated}"
        )
    if unidentified_shortfall and marker.get("allow_unidentified") is not True:
        return (
            f"{DRAIN_MARKER_KEY} must set allow_unidentified for "
            f"{unidentified_shortfall} row(s) without a task id"
        )
    return None


def validate_state_transition(
    new_state: dict[str, Any],
    previous_state: dict[str, Any] | None,
) -> None:
    """Reject a commit that makes live task identities disappear unaudited.

    The guard is identity aware rather than count based: completing the final
    task is a status transition and stays legal, and archiving a row that was
    already terminal stays legal. What is refused is a commit in which task
    identities that were still live simply vanish -- one at a time, or as the
    mass replacement that collapsed the authoritative journal to an empty board.
    Real removal is still possible, but only with an explicit audited drain
    marker that names exactly the tasks being dropped.
    """

    if not isinstance(new_state, dict):
        raise TaskStateStoreError("task-state commit must contain an object state")
    if previous_state is None:
        return  # First bootstrap has no predecessor identity to preserve.
    previous = _task_census(previous_state)
    if not previous["nonterminal_ids"] and not previous["unidentified_nonterminal"]:
        return  # A fully terminal board may drain without further evidence.
    current = _task_census(new_state)
    removed = sorted(previous["nonterminal_ids"] - current["ids"])
    unidentified_shortfall = max(
        0,
        previous["unidentified_nonterminal"] - current["unidentified_nonterminal"],
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
        f"[{detail}] leaving {len(survivors)} of "
        f"{len(previous['nonterminal_ids']) + previous['unidentified_nonterminal']} "
        f"live tasks; {rejection}"
    )


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
        previous_state = events[-1]["state"] if events else None
        validate_state_transition(state, previous_state)
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
    census = _task_census(projected)
    return {
        "ok": bool(events) and projected_sha256 == expected_sha256,
        "event_count": len(events),
        "last_event_id": events[-1]["event_id"] if events else None,
        "projected_state_sha256": projected_sha256,
        "expected_state_sha256": expected_sha256,
        # Surfaced so a collapsed board is visible even when parity itself holds.
        "nonterminal_task_count": len(census["nonterminal_ids"])
        + census["unidentified_nonterminal"],
    }
