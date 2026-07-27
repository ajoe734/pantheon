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
import mmap
import os
import stat
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


EVENT_VERSION = 1
EVENT_TYPE_STATE_COMMITTED = "task_state_committed"

# Replay acceleration. The journal is append-only, so the validation verdict for
# a byte range never changes once that range stops growing. The checkpoint
# sidecar records the projected head for an exact byte prefix and is accepted
# only when a SHA-256 over those same bytes still matches, so a reader verifies
# every byte of the journal on every read and re-parses only the new tail.
CHECKPOINT_VERSION = 1
CHECKPOINT_SUFFIX = ".checkpoint.json"
FULL_REPLAY_ENV = "PANTHEON_TASK_STATE_STORE_FULL_REPLAY"

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


def _read_journal_bytes(path: Path) -> bytes:
    if not path.exists():
        return b""
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
    return b"".join(chunks)


def _load_events_unlocked(path: Path) -> list[dict[str, Any]]:
    payload = _read_journal_bytes(path)
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


def _checkpoint_path(event_path: Path) -> Path:
    return event_path.with_name(f"{event_path.name}{CHECKPOINT_SUFFIX}")


def _full_replay_forced() -> bool:
    return str(os.environ.get(FULL_REPLAY_ENV) or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _read_checkpoint(event_path: Path) -> dict[str, Any] | None:
    """Return a structurally usable checkpoint, or ``None`` to force full replay.

    Every failure mode here -- missing, unreadable, wrong schema, wrong version,
    a symlink where a regular file belongs -- degrades to a full replay rather
    than raising, because the checkpoint is a derived cache and never the
    authority for what the journal contains.
    """

    if _full_replay_forced():
        return None
    path = _checkpoint_path(event_path)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            return None
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
    except OSError:
        return None
    finally:
        os.close(descriptor)
    try:
        checkpoint = json.loads(b"".join(chunks).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(checkpoint, dict) or checkpoint.get("version") != CHECKPOINT_VERSION:
        return None
    if not isinstance(checkpoint.get("prefix_sha256"), str):
        return None
    if not isinstance(checkpoint.get("prefix_bytes"), int) or checkpoint["prefix_bytes"] <= 0:
        return None
    if not isinstance(checkpoint.get("event_count"), int) or checkpoint["event_count"] <= 0:
        return None
    if not isinstance(checkpoint.get("last_event"), dict):
        return None
    return checkpoint


def _write_checkpoint(
    event_path: Path,
    *,
    prefix_bytes: int,
    prefix_sha256: str,
    event_count: int,
    last_event: dict[str, Any] | None,
) -> None:
    """Atomically replace the derived checkpoint; never fail the caller for it."""

    if _full_replay_forced() or event_count <= 0 or last_event is None:
        return
    record = {
        "version": CHECKPOINT_VERSION,
        "prefix_bytes": prefix_bytes,
        "prefix_sha256": prefix_sha256,
        "event_count": event_count,
        "last_event": last_event,
    }
    target = _checkpoint_path(event_path)
    # Readers hold the store lock shared, so two of them can refresh the cache
    # at once; each needs its own staging name before the atomic replace.
    temporary = target.with_name(f"{target.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_TRUNC
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(temporary, flags, 0o600)
        try:
            body = canonical_json_bytes(record)
            offset = 0
            while offset < len(body):
                offset += os.write(descriptor, body[offset:])
        finally:
            os.close(descriptor)
        os.replace(temporary, target)
    except OSError:
        # A cache that cannot be refreshed only costs the next reader a full
        # replay; it must never turn a healthy journal read into a failure.
        try:
            os.unlink(temporary)
        except OSError:
            pass


def _resume_point(
    payload: bytes | memoryview,
    checkpoint: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Return the validated head a checkpoint still describes, if any.

    The checkpoint is honoured only when a SHA-256 over the exact byte prefix it
    claims still matches the journal on disk. Validation is a pure function of
    those bytes, so an unchanged prefix means the earlier verdict still holds
    and only the tail needs to be parsed again.
    """

    if checkpoint is None:
        return None
    prefix_bytes = int(checkpoint["prefix_bytes"])
    if prefix_bytes > len(payload) or payload[prefix_bytes - 1 : prefix_bytes] != b"\n":
        return None
    prefix_digest = hashlib.sha256(memoryview(payload)[:prefix_bytes])
    if prefix_digest.hexdigest() != checkpoint["prefix_sha256"]:
        return None
    last_event = checkpoint["last_event"]
    try:
        # A prefix digest proves which journal bytes the cache describes, but
        # not which event is at that prefix's head. A checkpoint-only writer
        # could otherwise pair the real prefix digest with a different,
        # internally valid event and make the accelerated read project forged
        # state. Bind the cached event to the actual final JSONL record.
        head_end = prefix_bytes - 1
        previous_newline = head_end - 1
        while previous_newline >= 0 and payload[previous_newline] != 0x0A:
            previous_newline -= 1
        raw_head = bytes(memoryview(payload)[previous_newline + 1 : head_end])
        actual_last_event = json.loads(raw_head.decode("utf-8"))
        if actual_last_event != last_event:
            return None
        validate_event(
            actual_last_event,
            expected_sequence=int(checkpoint["event_count"]),
            previous_sha256=actual_last_event.get("previous_event_sha256"),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, TaskStateStoreError):
        return None
    return {
        "offset": prefix_bytes,
        "event_count": int(checkpoint["event_count"]),
        "last_event": actual_last_event,
        "digest": prefix_digest,
    }


def _snapshot_from_payload(
    payload: bytes | memoryview,
    checkpoint: dict[str, Any] | None,
) -> dict[str, Any]:
    resume = _resume_point(payload, checkpoint)
    if resume is None:
        offset = 0
        event_count = 0
        last_event: dict[str, Any] | None = None
        previous_sha256: str | None = None
        digest = hashlib.sha256()
    else:
        offset = int(resume["offset"])
        event_count = int(resume["event_count"])
        last_event = resume["last_event"]
        previous_sha256 = str(last_event["event_sha256"])
        digest = resume["digest"]
    # Extending the prefix hasher keeps the whole-journal digest to a single
    # pass over the bytes even when the checkpoint already covered most of them.
    digest = digest.copy()
    digest.update(memoryview(payload)[offset:])
    replayed_from_checkpoint = resume is not None
    revalidated = 0

    # Only the unvalidated tail is materialized; when the checkpoint already
    # covers the whole journal this is empty.
    for raw_line in bytes(payload[offset:]).splitlines():
        if not raw_line.strip():
            continue
        try:
            event = json.loads(raw_line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TaskStateStoreError(
                f"invalid task-state event at line {event_count + 1}: {exc}"
            ) from exc
        last_event = validate_event(
            event,
            expected_sequence=event_count + 1,
            previous_sha256=previous_sha256,
        )
        previous_sha256 = str(last_event["event_sha256"])
        event_count += 1
        revalidated += 1

    common = {
        "byte_size": len(payload),
        "journal_digest": digest,
        "revalidated_events": revalidated,
        "resumed_from_checkpoint": replayed_from_checkpoint,
    }
    if last_event is None:
        return {
            "event_count": 0,
            "last_event": None,
            "last_event_id": None,
            "last_event_sha256": None,
            "state": {},
            "state_sha256": sha256_json({}),
            **common,
        }
    return {
        "event_count": event_count,
        "last_event": last_event,
        "last_event_id": str(last_event["event_id"]),
        "last_event_sha256": str(last_event["event_sha256"]),
        "state": last_event["state"],
        "state_sha256": str(last_event["state_sha256"]),
        **common,
    }


@contextmanager
def _journal_view(path: Path):
    """Expose the journal as a read-only buffer without copying it into the heap.

    Hashing every byte is what lets the checkpoint be trusted, but reading a
    ~160MB journal into a ``bytes`` object costs more than the hash itself. The
    file cannot change underneath the mapping: every reader holds the shared
    store lock and every writer holds it exclusively.
    """

    if not path.exists():
        yield memoryview(b"")
        return
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise TaskStateStoreError(f"task-state event log must be a regular file: {path}")
        if info.st_size == 0:
            yield memoryview(b"")
            return
        with mmap.mmap(descriptor, info.st_size, access=mmap.ACCESS_READ) as mapped:
            with memoryview(mapped) as view:
                yield view
    finally:
        os.close(descriptor)


def _load_snapshot_unlocked(event_path: Path) -> dict[str, Any]:
    checkpoint = _read_checkpoint(event_path)
    with _journal_view(event_path) as payload:
        snapshot = _snapshot_from_payload(payload, checkpoint)
    if snapshot["revalidated_events"]:
        _write_checkpoint(
            event_path,
            prefix_bytes=int(snapshot["byte_size"]),
            prefix_sha256=snapshot["journal_digest"].hexdigest(),
            event_count=int(snapshot["event_count"]),
            last_event=snapshot["last_event"],
        )
    return snapshot


def load_snapshot(path: str | Path) -> dict[str, Any]:
    """Validate the journal once and return its head as one consistent snapshot.

    Callers that need both the projected state and a projection report must use
    this instead of pairing ``load_events``/``project_latest_state`` with a
    second ``verify_projection`` read. Two reads take two independent lock
    windows, so a commit landing between them produced the observed transient
    report where ``event_count`` came from one journal generation and the
    compared digest from another.
    """

    event_path = _prepare_parent(Path(path))
    with _store_lock(event_path, shared=True):
        snapshot = _load_snapshot_unlocked(event_path)
    # The rolling hasher is an internal continuation for the append path; a
    # public snapshot has to stay a plain, copyable record.
    snapshot.pop("journal_digest", None)
    snapshot["state"] = copy.deepcopy(snapshot["state"])
    snapshot["last_event"] = copy.deepcopy(snapshot["last_event"])
    return snapshot


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
        snapshot = _load_snapshot_unlocked(event_path)
        head = snapshot["last_event"]
        previous_state = head["state"] if head else None
        validate_state_transition(state, previous_state)
        state_sha256 = sha256_json(state)
        if head is not None and head.get("state_sha256") == state_sha256:
            return copy.deepcopy(head)
        sequence = int(snapshot["event_count"]) + 1
        previous_event_sha256 = snapshot["last_event_sha256"]
        event: dict[str, Any] = {
            "version": EVENT_VERSION,
            "type": EVENT_TYPE_STATE_COMMITTED,
            "sequence": sequence,
            "committed_at": committed_at or utc_now(),
            "source": str(source or "unknown").strip() or "unknown",
            "previous_event_sha256": previous_event_sha256,
            "state_sha256": state_sha256,
            "state": copy.deepcopy(state),
        }
        event_sha256 = sha256_json(event)
        event["event_sha256"] = event_sha256
        event["event_id"] = f"task-state-{event_sha256}"
        validate_event(
            event,
            expected_sequence=sequence,
            previous_sha256=previous_event_sha256,
        )
        payload = canonical_json_bytes(event) + b"\n"
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
            offset = 0
            while offset < len(payload):
                offset += os.write(descriptor, payload[offset:])
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        _fsync_directory(event_path.parent)
        _verify_append_readback(
            event_path,
            expected_size=int(snapshot["byte_size"]) + len(payload),
            offset=int(snapshot["byte_size"]),
            payload=payload,
        )
        # The digest of the journal after this append is the pre-append digest
        # extended by the bytes just written -- no second pass over the file.
        journal_digest = snapshot["journal_digest"].copy()
        journal_digest.update(payload)
        _write_checkpoint(
            event_path,
            prefix_bytes=int(snapshot["byte_size"]) + len(payload),
            prefix_sha256=journal_digest.hexdigest(),
            event_count=sequence,
            last_event=event,
        )
        return copy.deepcopy(event)


def _verify_append_readback(
    event_path: Path,
    *,
    expected_size: int,
    offset: int,
    payload: bytes,
) -> None:
    """Confirm the appended bytes reached the journal without replaying it.

    The previous readback replayed and revalidated the entire journal to inspect
    its last line, so every commit paid a second full-file cost while holding the
    exclusive store lock. The durability question is narrower: are the bytes this
    call wrote present, complete, and at the end of the file?
    """

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(event_path, flags)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise TaskStateStoreError(
                f"task-state event log must be a regular file: {event_path}"
            )
        if info.st_size != expected_size:
            raise TaskStateStoreError("task-state append readback mismatch")
        chunks: list[bytes] = []
        remaining = len(payload)
        position = offset
        while remaining > 0:
            chunk = os.pread(descriptor, remaining, position)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
            position += len(chunk)
    finally:
        os.close(descriptor)
    if b"".join(chunks) != payload:
        raise TaskStateStoreError("task-state append readback mismatch")


def verify_snapshot(snapshot: dict[str, Any], expected_state: dict[str, Any]) -> dict[str, Any]:
    """Compare one already-validated journal snapshot against a projection.

    Pure and lock-free by construction: the digests, the event count, and the
    last event id all describe the same journal generation, so a commit landing
    concurrently can no longer split the report across two generations.
    """

    projected_sha256 = str(snapshot["state_sha256"])
    expected_sha256 = sha256_json(expected_state)
    census = _task_census(snapshot["state"])
    return {
        "ok": bool(snapshot["event_count"]) and projected_sha256 == expected_sha256,
        "event_count": int(snapshot["event_count"]),
        "last_event_id": snapshot["last_event_id"],
        "projected_state_sha256": projected_sha256,
        "expected_state_sha256": expected_sha256,
        # Surfaced so a collapsed board is visible even when parity itself holds.
        "nonterminal_task_count": len(census["nonterminal_ids"])
        + census["unidentified_nonterminal"],
    }


def verify_projection(path: str | Path, expected_state: dict[str, Any]) -> dict[str, Any]:
    return verify_snapshot(load_snapshot(path), expected_state)
