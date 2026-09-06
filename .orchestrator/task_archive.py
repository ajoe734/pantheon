#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
from contextlib import nullcontext
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping
from urllib.parse import quote

from common import canonical_task_state_lock_file, write_json as durable_write_json

ROOT = Path(__file__).resolve().parents[1]


def status_root() -> Path:
    raw = str(os.environ.get("PANTHEON_STATUS_ROOT") or "").strip()
    if not raw:
        return ROOT
    return Path(os.path.expanduser(raw)).resolve()


STATUS_ROOT = status_root()
ARCHIVE_DIR = STATUS_ROOT / "ai-task-archive"
ARCHIVE_TASKS_DIR = ARCHIVE_DIR / "tasks"
ARCHIVE_INDEX_FILE = ARCHIVE_DIR / "index.json"
STATUS_FILE = STATUS_ROOT / "ai-status.json"

ARCHIVE_VERSION = 1
TERMINAL_STATUS_DONE = "done"
TERMINAL_OUTCOME_COMPLETED = "completed"
TERMINAL_OUTCOME_SUPERSEDED = "superseded"
COMPLETION_TRACKS = frozenset({"functional", "hosted"})
COMPLETION_TRACK_STATUSES = frozenset(
    {"pending", "in_progress", "done", "external_wait"}
)
DEFAULT_RECENT_LIMIT = 20
ARCHIVE_CORRECTION_VERSION = 1
STATUS_ARCHIVE_OUTBOX_SCHEMA_VERSION = 2
WORKER_WORKTREE_PREFIX = "/tmp/pantheon-worker-worktrees/pantheon"


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_task_archive_file_safe(path: Path) -> str:
    import stat
    import errno
    try:
        st = os.lstat(path)
        if stat.S_ISLNK(st.st_mode):
            raise RuntimeError(f"archive-leaf cannot be a symlink: {path}")
        if not stat.S_ISREG(st.st_mode):
            raise RuntimeError(f"archive-leaf must be a regular file: {path}")
    except FileNotFoundError:
        raise
    except OSError as e:
        raise RuntimeError(f"Failed to lstat archive-leaf {path}: {e}")

    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as e:
        if e.errno == errno.ELOOP:
            raise RuntimeError(f"archive-leaf cannot be a symlink: {path}")
        raise

    try:
        fst = os.fstat(fd)
        if not stat.S_ISREG(fst.st_mode):
            raise RuntimeError(f"archive-leaf fd must be a regular file: {path}")
        with open(fd, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        try:
            os.close(fd)
        except OSError:
            pass
        raise e


def read_task_archive_file_bytes_safe(path: Path) -> bytes:
    import stat
    import errno
    try:
        st = os.lstat(path)
        if stat.S_ISLNK(st.st_mode):
            raise RuntimeError(f"archive-leaf cannot be a symlink: {path}")
        if not stat.S_ISREG(st.st_mode):
            raise RuntimeError(f"archive-leaf must be a regular file: {path}")
    except FileNotFoundError:
        raise
    except OSError as e:
        raise RuntimeError(f"Failed to lstat archive-leaf {path}: {e}")

    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as e:
        if e.errno == errno.ELOOP:
            raise RuntimeError(f"archive-leaf cannot be a symlink: {path}")
        raise

    try:
        fst = os.fstat(fd)
        if not stat.S_ISREG(fst.st_mode):
            raise RuntimeError(f"archive-leaf fd must be a regular file: {path}")
        with open(fd, "rb") as f:
            return f.read()
    except Exception as e:
        try:
            os.close(fd)
        except OSError:
            pass
        raise e


def _archive_fault(point: str) -> None:
    if str(os.environ.get("LOOP_TEST_ARCHIVE_SIGKILL_AFTER") or "").strip() == point:
        os.kill(os.getpid(), 9)


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def load_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return deepcopy(default)
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return deepcopy(default)
    return json.loads(text)


def write_json(path: Path, payload: Any) -> None:
    with canonical_task_state_lock_file(
        STATUS_FILE,
        shared=False,
        nonblocking=False,
    ):
        durable_write_json(path, payload)


def normalize_task_id(task_id: str | None) -> str:
    return str(task_id or "").strip()


def normalize_archive_review_file(review_file: str | None) -> str:
    raw = str(review_file or "").strip()
    path = PurePosixPath(raw)
    if (
        not raw
        or path.is_absolute()
        or "\\" in raw
        or raw != path.as_posix()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise RuntimeError(
            "archive review_file correction must be a normalized repository-relative path"
        )
    return raw


def validate_archive_correction_context(value: Any) -> dict[str, Any]:
    expected_keys = {
        "version",
        "corrected_at",
        "actor",
        "reason",
        "field",
        "from",
        "to",
        "evidence_sha256",
    }
    if not isinstance(value, dict) or set(value) != expected_keys:
        raise RuntimeError("archive correction_context schema is not exact")
    if value.get("version") != ARCHIVE_CORRECTION_VERSION:
        raise RuntimeError("archive correction_context version is invalid")
    if not str(value.get("corrected_at") or "").strip():
        raise RuntimeError("archive correction_context corrected_at is required")
    if not str(value.get("actor") or "").strip():
        raise RuntimeError("archive correction_context actor is required")
    if not str(value.get("reason") or "").strip():
        raise RuntimeError("archive correction_context reason is required")
    if value.get("field") != "task.review_file":
        raise RuntimeError("archive correction_context field is invalid")
    if not Path(str(value.get("from") or "")).is_absolute():
        raise RuntimeError("archive correction_context source must be absolute")
    normalize_archive_review_file(value.get("to"))
    digest = str(value.get("evidence_sha256") or "").strip()
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise RuntimeError("archive correction_context evidence_sha256 is invalid")
    return value


def task_status(task: dict[str, Any] | None) -> str:
    if not isinstance(task, dict):
        return ""
    return str(task.get("status") or "").strip().lower()


def terminal_outcome_for(task: dict[str, Any] | None) -> str:
    if not isinstance(task, dict):
        return ""
    outcome = str(task.get("terminal_outcome") or "").strip().lower()
    if outcome:
        return outcome
    if task_status(task) == TERMINAL_STATUS_DONE:
        return TERMINAL_OUTCOME_COMPLETED
    return ""


def is_terminal_task(task: dict[str, Any] | None) -> bool:
    return task_status(task) == TERMINAL_STATUS_DONE


def task_satisfies_dependency(task: dict[str, Any] | None) -> bool:
    return is_terminal_task(task) and terminal_outcome_for(task) != TERMINAL_OUTCOME_SUPERSEDED


def compact_completion_tracks(value: object) -> dict[str, dict[str, str]]:
    """Keep completion-track status when a terminal task leaves the active board.

    ``terminal_facts`` are intentionally much smaller than archived task
    snapshots, but a consumer may depend on a named ``functional`` or
    ``hosted`` track.  Dropping that status turns an already-recorded milestone
    into ``pending`` forever once the producer is archived.  Preserve only the
    bounded status/timestamp fields needed by the scheduler; evidence and
    narrative remain in the immutable archive snapshot.
    """

    if value in (None, {}):
        return {}
    if not isinstance(value, Mapping):
        raise RuntimeError("completion_tracks must be an object")
    compact: dict[str, dict[str, str]] = {}
    for raw_track, raw_record in value.items():
        track = str(raw_track or "").strip().lower()
        if track not in COMPLETION_TRACKS:
            raise RuntimeError(f"completion track is invalid: {raw_track}")
        if not isinstance(raw_record, Mapping):
            raise RuntimeError(f"completion track {track} is invalid")
        status = str(raw_record.get("status") or "pending").strip().lower()
        if status not in COMPLETION_TRACK_STATUSES:
            raise RuntimeError(f"completion track {track} status is invalid: {status}")
        record = {"status": status}
        updated_at = str(raw_record.get("updated_at") or "").strip()
        if updated_at:
            record["updated_at"] = updated_at
        compact[track] = record
    return compact


def dependency_track_for(task: Mapping[str, Any] | None, dependency_id: str) -> str | None:
    """Return the explicit dependency track, preserving terminal compatibility.

    Existing tasks keep their string ``depends_on`` semantics.  A task may opt
    one dependency into a named completion track through ``dependency_tracks``;
    invalid values fail closed instead of silently becoming terminal gates.
    """

    if not isinstance(task, Mapping):
        return "terminal"
    tracks = task.get("dependency_tracks")
    if not isinstance(tracks, Mapping) or dependency_id not in tracks:
        return "terminal"
    value = str(tracks.get(dependency_id) or "").strip().lower()
    return value if value in COMPLETION_TRACKS else None


def completion_track_status(task: Mapping[str, Any] | None, track: str) -> str:
    """Read one non-terminal completion track without inferring success."""

    if track not in COMPLETION_TRACKS:
        return "invalid"
    tracks = task.get("completion_tracks") if isinstance(task, Mapping) else None
    record = tracks.get(track) if isinstance(tracks, Mapping) else None
    if not isinstance(record, Mapping):
        return "pending"
    status = str(record.get("status") or "pending").strip().lower()
    return status or "pending"


def dependency_satisfied_for(
    consumer_task: Mapping[str, Any] | None,
    dependency_id: str,
    resolver: Any,
    done_statuses: set[str] | None = None,
) -> bool:
    """Evaluate a dependency using the consumer's explicit completion track."""

    track = dependency_track_for(consumer_task, dependency_id)
    if track is None:
        return False
    dependency = resolver.get(dependency_id)
    if dependency is None:
        return False
    if track == "terminal":
        allowed = done_statuses or {TERMINAL_STATUS_DONE}
        return (
            task_status(dependency) in allowed
            and task_satisfies_dependency(dependency)
        )
    return completion_track_status(dependency, track) == TERMINAL_STATUS_DONE


def archive_task_path(task_id: str | None) -> Path:
    normalized = normalize_task_id(task_id)
    if not normalized:
        raise ValueError("task_id is required for archive lookup")
    slug = quote(normalized, safe="-_.")
    return ARCHIVE_TASKS_DIR / f"{slug}.json"


def archive_tasks_dir_for_status_root(status_root: str | Path) -> Path:
    return Path(status_root).expanduser().resolve() / "ai-task-archive" / "tasks"


def archive_task_path_in_dir(task_id: str | None, archive_tasks_dir: str | Path) -> Path:
    normalized = normalize_task_id(task_id)
    if not normalized:
        raise ValueError("task_id is required for archive lookup")
    slug = quote(normalized, safe="-_.")
    return Path(archive_tasks_dir).expanduser().resolve() / f"{slug}.json"


def archive_display_path(path: Path) -> str:
    for root in (STATUS_ROOT, ROOT):
        try:
            return str(path.relative_to(root))
        except ValueError:
            continue
    return str(path)


def default_archive_index() -> dict[str, Any]:
    return {
        "version": ARCHIVE_VERSION,
        "updated_at": None,
        "counts": {
            "total": 0,
            TERMINAL_OUTCOME_COMPLETED: 0,
            TERMINAL_OUTCOME_SUPERSEDED: 0,
        },
        "recent_terminal_ids": [],
    }


def load_archive_index() -> dict[str, Any]:
    with canonical_task_state_lock_file(
        STATUS_FILE,
        shared=True,
        nonblocking=False,
    ):
        payload = load_json(ARCHIVE_INDEX_FILE, default_archive_index()) or default_archive_index()
    counts = payload.setdefault("counts", {})
    counts["total"] = int(counts.get("total") or 0)
    counts[TERMINAL_OUTCOME_COMPLETED] = int(counts.get(TERMINAL_OUTCOME_COMPLETED) or 0)
    counts[TERMINAL_OUTCOME_SUPERSEDED] = int(counts.get(TERMINAL_OUTCOME_SUPERSEDED) or 0)
    payload["recent_terminal_ids"] = [
        normalize_task_id(item)
        for item in payload.get("recent_terminal_ids", [])
        if normalize_task_id(item)
    ]
    payload["version"] = ARCHIVE_VERSION
    payload.setdefault("updated_at", None)
    return payload


def save_archive_index(index: dict[str, Any]) -> None:
    payload = deepcopy(index)
    payload["version"] = ARCHIVE_VERSION
    write_json(ARCHIVE_INDEX_FILE, payload)


def get_outbox_snapshots() -> dict[str, Any]:
    outbox_snapshots = {}
    if STATUS_FILE.exists():
        try:
            status_data = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
            if isinstance(status_data, dict):
                outbox = status_data.get("status_archive_outbox")
                if outbox not in (None, {}, []):
                    validated_outbox = validate_status_archive_outbox(outbox)
                    for item in validated_outbox["snapshots"]:
                        t_id = normalize_task_id(item.get("task_id"))
                        if t_id:
                            outbox_snapshots[t_id] = item
        except Exception:
            pass
    return outbox_snapshots


def validate_archive_snapshot(
    snapshot: Any,
    filename_task_id: str | None = None,
    outbox_snapshots: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(snapshot, dict):
        raise RuntimeError("snapshot is not a JSON object")

    task_id = normalize_task_id(
        snapshot.get("task_id")
        or ((snapshot.get("task") or {}).get("id"))
        or snapshot.get("id")
    )
    if not task_id:
        raise RuntimeError("snapshot is missing task id")

    if filename_task_id:
        expected_normalized = normalize_task_id(filename_task_id)
        if task_id != expected_normalized:
            raise RuntimeError(
                f"snapshot task_id {task_id} does not match expected filename task_id {expected_normalized}"
            )

    # Enforce modern or legacy contract or proven outbox provenance
    valid_contract = is_valid_modern_contract(snapshot) or is_valid_legacy_contract(snapshot)
    if not valid_contract:
        if outbox_snapshots is None:
            outbox_snapshots = get_outbox_snapshots()
        if task_id not in outbox_snapshots:
            raise RuntimeError(
                f"snapshot for {task_id} does not satisfy any valid contract "
                f"and lacks proven durable outbox provenance"
            )
        # Validate the snapshot content itself matches the outbox snapshot exactly
        outbox_snap = outbox_snapshots[task_id]
        if _canonical_json_sha256(snapshot) != _canonical_json_sha256(outbox_snap):
            raise RuntimeError(
                f"snapshot for {task_id} content does not match the outbox snapshot exactly"
            )

    # STRICT SNAPSHOT VALIDATION
    version = snapshot.get("version")
    if version is not None:
        try:
            if int(version) != ARCHIVE_VERSION:
                raise RuntimeError(f"snapshot has invalid version: {version}")
        except (ValueError, TypeError):
            raise RuntimeError(f"snapshot has malformed version: {version}")

    outcome = str(snapshot.get("terminal_outcome") or "").strip().lower() or TERMINAL_OUTCOME_COMPLETED
    if outcome not in {TERMINAL_OUTCOME_COMPLETED, TERMINAL_OUTCOME_SUPERSEDED}:
        raise RuntimeError(f"snapshot has invalid terminal_outcome: {outcome}")

    status_val = snapshot.get("terminal_status") or (snapshot.get("task") or {}).get("status")
    if status_val is not None and str(status_val).strip().lower() != "done":
        raise RuntimeError(f"snapshot has invalid status: {status_val}")

    archived_at = str(snapshot.get("archived_at") or "").strip()
    if not archived_at:
        raise RuntimeError("snapshot is missing archived_at")

    return snapshot


def load_archived_snapshot(task_id: str | None) -> dict[str, Any] | None:
    normalized = normalize_task_id(task_id)
    if not normalized:
        return None
    path = archive_task_path(normalized)
    with canonical_task_state_lock_file(
        STATUS_FILE,
        shared=True,
        nonblocking=False,
    ):
        try:
            text = read_task_archive_file_safe(path)
            if not text.strip():
                return None
            snapshot = json.loads(text)
        except FileNotFoundError:
            return None
        except Exception as e:
            raise RuntimeError(f"Failed to load archive snapshot safely: {e}")
    return validate_archive_snapshot(snapshot, filename_task_id=normalized)


def load_archived_raw_bytes(task_id: str | None) -> bytes | None:
    normalized = normalize_task_id(task_id)
    if not normalized:
        return None
    path = archive_task_path(normalized)
    with canonical_task_state_lock_file(
        STATUS_FILE,
        shared=True,
        nonblocking=False,
    ):
        try:
            return read_task_archive_file_bytes_safe(path)
        except FileNotFoundError:
            return None
        except Exception as e:
            raise RuntimeError(f"Failed to load archive bytes safely: {e}")



def correct_archived_task_review_file(
    task_id: str,
    review_file: str,
    *,
    actor: str,
    reason: str,
    evidence_sha256: str,
    corrected_at: str | None = None,
    canonical_lock_held: bool = False,
) -> dict[str, Any]:
    normalized_task_id = normalize_task_id(task_id)
    target = normalize_archive_review_file(review_file)
    actor = str(actor or "").strip()
    reason = str(reason or "").strip()
    digest = str(evidence_sha256 or "").strip()
    if not normalized_task_id:
        raise RuntimeError("archive correction task_id is required")
    if not actor or not reason:
        raise RuntimeError("archive correction actor and reason are required")
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise RuntimeError("archive correction evidence_sha256 is invalid")

    path = archive_task_path(normalized_task_id)
    lock_context = (
        nullcontext()
        if canonical_lock_held
        else canonical_task_state_lock_file(
            STATUS_FILE,
            shared=False,
            nonblocking=False,
        )
    )
    with lock_context:
        try:
            snapshot = json.loads(read_task_archive_file_safe(path))
        except FileNotFoundError as exc:
            raise RuntimeError(
                f"archive snapshot is missing for {normalized_task_id}"
            ) from exc
        validate_archive_snapshot(snapshot, filename_task_id=normalized_task_id)
        if not is_valid_modern_contract(snapshot):
            raise RuntimeError(
                f"archive correction requires a modern snapshot for {normalized_task_id}"
            )

        task = snapshot["task"]
        current = str(task.get("review_file") or "").strip()
        existing_context = snapshot.get("correction_context")
        if current == target:
            context = validate_archive_correction_context(existing_context)
            if (
                context.get("to") != target
                or context.get("evidence_sha256") != digest
            ):
                raise RuntimeError(
                    f"archive correction replay conflicts for {normalized_task_id}"
                )
            return deepcopy(snapshot)
        if existing_context is not None:
            raise RuntimeError(
                f"archive snapshot already has a different correction for {normalized_task_id}"
            )

        task_slug = re.sub(r"[^a-z0-9]+", "-", normalized_task_id.lower()).strip("-")
        expected_prefix = f"{WORKER_WORKTREE_PREFIX}/{task_slug}/"
        if (
            not Path(current).is_absolute()
            or not current.startswith(expected_prefix)
            or not current.endswith(f"/{target}")
        ):
            raise RuntimeError(
                f"archive review_file source is not the expected disposable worker path for {normalized_task_id}"
            )

        corrected = deepcopy(snapshot)
        corrected["task"]["review_file"] = target
        corrected["correction_context"] = {
            "version": ARCHIVE_CORRECTION_VERSION,
            "corrected_at": corrected_at or iso_now(),
            "actor": actor,
            "reason": reason,
            "field": "task.review_file",
            "from": current,
            "to": target,
            "evidence_sha256": digest,
        }
        if not is_valid_modern_contract(corrected):
            raise RuntimeError(
                f"corrected archive snapshot is invalid for {normalized_task_id}"
            )
        durable_write_json(path, corrected)
        readback = json.loads(read_task_archive_file_safe(path))
        if _canonical_json_sha256(readback) != _canonical_json_sha256(corrected):
            raise RuntimeError(
                f"archive correction readback mismatch for {normalized_task_id}"
            )
        return deepcopy(corrected)


def task_from_archive_snapshot(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize modern nested and legacy top-level archive task shapes."""

    nested = snapshot.get("task")
    if isinstance(nested, dict):
        return deepcopy(nested)
    task_id = normalize_task_id(snapshot.get("task_id") or snapshot.get("id"))
    if not task_id:
        return None
    task = deepcopy(snapshot)
    task["id"] = task_id
    task.pop("task_id", None)
    task.pop("archived_at", None)
    task.pop("version", None)
    task.pop("terminal_status", None)
    task.pop("handoffs", None)
    task.pop("blockers", None)
    task.setdefault(
        "status",
        snapshot.get("terminal_status")
        or (TERMINAL_STATUS_DONE if snapshot.get("terminal_outcome") else None),
    )
    if not task.get("status"):
        return None
    return task


def compact_terminal_summary(snapshot: dict[str, Any]) -> dict[str, Any]:
    task = task_from_archive_snapshot(snapshot) or {}
    task_id = normalize_task_id(
        snapshot.get("task_id") or snapshot.get("id") or task.get("id")
    )
    return {
        "task_id": task_id,
        "title": task.get("title"),
        "summary_zh": task.get("summary_zh"),
        "phase": task.get("phase"),
        "owner": task.get("owner"),
        "reviewer": task.get("reviewer"),
        "status": task.get("status"),
        "terminal_outcome": snapshot.get("terminal_outcome") or terminal_outcome_for(task),
        "last_update": task.get("last_update"),
        "archived_at": snapshot.get("archived_at"),
        "next": task.get("next"),
        "snapshot_path": archive_display_path(archive_task_path(task_id)),
    }


def recent_terminal_summaries(limit: int = DEFAULT_RECENT_LIMIT) -> list[dict[str, Any]]:
    with canonical_task_state_lock_file(
        STATUS_FILE,
        shared=True,
        nonblocking=False,
    ):
        index = load_archive_index()
        summaries: list[dict[str, Any]] = []
        for task_id in index.get("recent_terminal_ids", [])[: max(0, int(limit))]:
            snapshot = load_archived_snapshot(task_id)
            if not snapshot:
                continue
            summaries.append(compact_terminal_summary(snapshot))
        return summaries


def is_valid_modern_contract(snapshot: Any) -> bool:
    if not isinstance(snapshot, dict):
        return False
    expected_keys = {
        "version",
        "task_id",
        "archived_at",
        "terminal_status",
        "terminal_outcome",
        "task",
        "handoffs",
        "blockers",
    }
    keys = set(snapshot.keys())
    allowed_keys = expected_keys | {"correction_context"}
    if not (expected_keys <= keys <= allowed_keys):
        return False
    task = snapshot.get("task")
    if not isinstance(task, dict):
        return False
    if str(task.get("status") or "").strip().lower() != "done":
        return False
    if str(task.get("id") or "").strip() != str(snapshot.get("task_id") or "").strip():
        return False
    if str(snapshot.get("terminal_status") or "").strip().lower() != "done":
        return False

    task_status_val = str(task.get("status") or "").strip().lower()
    outcome_val = str(task.get("terminal_outcome") or "").strip().lower()
    expected_outcome = ""
    if outcome_val:
        expected_outcome = outcome_val
    elif task_status_val == "done":
        expected_outcome = "completed"

    if str(snapshot.get("terminal_outcome") or "").strip().lower() != expected_outcome:
        return False
    if not isinstance(snapshot.get("handoffs"), list):
        return False
    if not isinstance(snapshot.get("blockers"), list):
        return False
    try:
        if int(snapshot.get("version") or 0) != 1:
            return False
    except Exception:
        return False
    if not str(snapshot.get("task_id") or "").strip():
        return False
    if not str(snapshot.get("archived_at") or "").strip():
        return False
    if "correction_context" in snapshot:
        try:
            validate_archive_correction_context(snapshot["correction_context"])
        except RuntimeError:
            return False
    return True


def is_valid_legacy_contract(snapshot: Any) -> bool:
    if not isinstance(snapshot, dict):
        return False
    # A legacy snapshot must have id or task_id
    task_id = normalize_task_id(snapshot.get("id") or snapshot.get("task_id"))
    if not task_id:
        return False
    # status must be done
    status_val = str(
        snapshot.get("status")
        or snapshot.get("terminal_status")
        or (TERMINAL_STATUS_DONE if snapshot.get("terminal_outcome") else "")
    ).strip().lower()
    if status_val != "done":
        return False
    # terminal_outcome must be completed or superseded
    outcome = str(
        snapshot.get("terminal_outcome")
        or ("completed" if status_val == "done" else "")
    ).strip().lower()
    if outcome not in {TERMINAL_OUTCOME_COMPLETED, TERMINAL_OUTCOME_SUPERSEDED}:
        return False
    # archived_at must be present
    archived_at = str(snapshot.get("archived_at") or "").strip()
    if not archived_at:
        return False
    return True


def _canonical_json_sha256(value: Any) -> str:
    import hashlib
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _is_status_archive_snapshot_valid(snapshot: Any) -> bool:
    if not isinstance(snapshot, dict):
        return False
    keys = set(snapshot.keys())
    expected = {
        "version",
        "task_id",
        "archived_at",
        "terminal_status",
        "terminal_outcome",
        "task",
        "handoffs",
        "blockers",
    }
    allowed = expected | {"correction_context"}
    if not (expected <= keys <= allowed):
        return False
    task = snapshot.get("task")
    valid = bool(
        snapshot.get("version") == 1
        and snapshot.get("terminal_status") == "done"
        and str(snapshot.get("task_id") or "").strip()
        and str(snapshot.get("archived_at") or "").strip()
        and isinstance(task, dict)
        and task.get("id") == snapshot.get("task_id")
        and task.get("status") == "done"
        and snapshot.get("terminal_outcome") in {"completed", "superseded"}
        and isinstance(snapshot.get("handoffs"), list)
        and isinstance(snapshot.get("blockers"), list)
    )
    if not valid:
        return False
    if "correction_context" in snapshot:
        try:
            validate_archive_correction_context(snapshot["correction_context"])
        except RuntimeError:
            return False
    return True


def status_archive_outbox_payload(
    snapshots: list[dict[str, Any]],
    *,
    archive_root: str,
) -> dict[str, Any]:
    """Build the sole durable archive-outbox contract.

    ``ai_status`` owns lifecycle mutation; this module owns the shared archive
    snapshot/index contract that both the writer and index rebuilder need.
    """

    snapshot_sha256s = {
        str(snapshot["task_id"]): _canonical_json_sha256(snapshot)
        for snapshot in snapshots
    }
    binding = {
        "archive_root": archive_root,
        "snapshots": snapshots,
        "snapshot_sha256s": snapshot_sha256s,
    }
    return {
        "schema_version": STATUS_ARCHIVE_OUTBOX_SCHEMA_VERSION,
        "transaction_id": "ai-status-archive-tx-" + _canonical_json_sha256(binding),
        **binding,
    }


def validate_status_archive_outbox(
    value: Any,
    *,
    expected_archive_root: str | None = None,
) -> dict[str, Any]:
    """Validate a receipt-bearing archive intent, upgrading v1 in flight."""

    if not isinstance(value, dict):
        raise RuntimeError("status archive outbox schema is not exact")
    if set(value) == {"schema_version", "transaction_id", "snapshots"} and value.get(
        "schema_version"
    ) == 1:
        snapshots = value.get("snapshots")
        if not isinstance(snapshots, list):
            raise RuntimeError("legacy status archive outbox is invalid")
        root = expected_archive_root or str(ARCHIVE_DIR.expanduser().resolve())
        return status_archive_outbox_payload(deepcopy(snapshots), archive_root=root)

    required = {
        "schema_version",
        "transaction_id",
        "archive_root",
        "snapshots",
        "snapshot_sha256s",
    }
    if set(value) != required:
        raise RuntimeError("status archive outbox schema is not exact")
    snapshots = value.get("snapshots")
    snapshot_sha256s = value.get("snapshot_sha256s")
    if (
        value.get("schema_version") != STATUS_ARCHIVE_OUTBOX_SCHEMA_VERSION
        or not isinstance(value.get("archive_root"), str)
        or not value["archive_root"].strip()
        or expected_archive_root is not None
        and value["archive_root"] != expected_archive_root
        or not isinstance(snapshots, list)
        or not snapshots
        or any(not _is_status_archive_snapshot_valid(snapshot) for snapshot in snapshots)
        or len({str(snapshot["task_id"]) for snapshot in snapshots}) != len(snapshots)
        or not isinstance(snapshot_sha256s, dict)
    ):
        raise RuntimeError("status archive outbox contract is invalid")
    expected_digests = {
        str(snapshot["task_id"]): _canonical_json_sha256(snapshot)
        for snapshot in snapshots
    }
    if snapshot_sha256s != expected_digests:
        raise RuntimeError("status archive outbox snapshot digest mismatch")
    binding = {
        "archive_root": value["archive_root"],
        "snapshots": snapshots,
        "snapshot_sha256s": snapshot_sha256s,
    }
    expected_id = "ai-status-archive-tx-" + _canonical_json_sha256(binding)
    if value.get("transaction_id") != expected_id:
        raise RuntimeError("status archive outbox digest mismatch")
    return value


def _rebuild_archive_index_locked(
    *,
    recent_limit: int = DEFAULT_RECENT_LIMIT,
    allow_uncommitted: bool = True,
    bypass_downgrade_check: bool = False,
    pinned_commit: str = "HEAD",
) -> dict[str, Any]:
    import subprocess
    import io
    summaries: list[dict[str, Any]] = []
    committed_snapshots: dict[str, str] = {}

    existing_index = load_json(ARCHIVE_INDEX_FILE, default=None)
    existing_total = 0
    if isinstance(existing_index, dict) and not bypass_downgrade_check:
        existing_total = int(existing_index.get("counts", {}).get("total") or 0)

    # Load archive outbox task IDs for provenance verification
    outbox_snapshots = {}
    if STATUS_FILE.exists():
        status_data = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
        if isinstance(status_data, dict):
            outbox = status_data.get("status_archive_outbox")
            if outbox not in (None, {}, []):
                # Validate the canonical outbox schema/digest/payload exactly
                validated_outbox = validate_status_archive_outbox(outbox)
                for item in validated_outbox["snapshots"]:
                    t_id = normalize_task_id(item.get("task_id"))
                    if t_id:
                        outbox_snapshots[t_id] = item

    is_git = False
    git_dir_exists = (STATUS_ROOT / ".git").exists() or any((p / ".git").exists() for p in STATUS_ROOT.parents)
    res_git = subprocess.run(
        ["git", "-C", str(STATUS_ROOT), "rev-parse", "--is-inside-work-tree"],
        capture_output=True,
        text=True,
        check=False,
    )
    if res_git.returncode == 0 and res_git.stdout.strip() == "true":
        is_git = True
    elif git_dir_exists:
        raise RuntimeError(f"Git probe failed inside git repository context: {res_git.stderr.strip()}")

    if is_git:
        # Verify pinned_commit is a valid git ref/commit
        res_verify = subprocess.run(
            ["git", "-C", str(STATUS_ROOT), "rev-parse", "--verify", f"{pinned_commit}^{{commit}}"],
            capture_output=True,
            text=True,
            check=False,
        )
        if res_verify.returncode != 0:
            raise RuntimeError(f"Invalid pinned commit/ref: {pinned_commit}")

        if pinned_commit == "HEAD":
            res_head = subprocess.run(
                ["git", "-C", str(STATUS_ROOT), "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                check=False,
            )
            if res_head.returncode != 0:
                raise RuntimeError("Failed to resolve HEAD")
            pinned_commit = res_head.stdout.strip()

        # Get list of files with uncommitted changes (modified, deleted, added, etc.)
        uncommitted_files = set()
        if allow_uncommitted:
            res_status = subprocess.run(
                ["git", "-C", str(STATUS_ROOT), "status", "--porcelain", "ai-task-archive/tasks/"],
                capture_output=True,
                text=True,
                check=False,
            )
            if res_status.returncode == 0:
                for line in res_status.stdout.splitlines():
                    if line.strip():
                        # Format: XY path
                        parts = line.strip().split(maxsplit=1)
                        if len(parts) == 2:
                            uncommitted_files.add(os.path.basename(parts[1]))

        # Get committed file list and SHAs from pinned commit
        res = subprocess.run(
            ["git", "-C", str(STATUS_ROOT), "ls-tree", "-r", pinned_commit, "ai-task-archive/tasks/"],
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode != 0:
            raise RuntimeError(f"git ls-tree failed for {pinned_commit}")

        sha_to_filename: dict[str, str] = {}
        for line in res.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split(maxsplit=3)
            if len(parts) < 4:
                raise RuntimeError(f"Malformed ls-tree row: {line}")
            mode, obj_type, sha, file_path = parts[0], parts[1], parts[2], parts[3]
            # OID validation: check SHA length and hex characters
            if len(sha) not in (40, 64) or not all(c in "0123456789abcdefABCDEF" for c in sha):
                raise RuntimeError(f"Invalid OID in ls-tree row: {line}")
            if obj_type != "blob":
                continue
            if file_path.endswith(".json"):
                basename = os.path.basename(file_path)
                if basename in uncommitted_files:
                    # Skip git version, we will read it from disk later
                    continue
                committed_snapshots[basename] = sha
                sha_to_filename[sha] = file_path

        # Read committed blobs using cat-file --batch
        if sha_to_filename:
            stdin_data = "\n".join(sha_to_filename.keys()).encode("utf-8") + b"\n"
            proc = subprocess.Popen(
                ["git", "-C", str(STATUS_ROOT), "cat-file", "--batch"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
            )
            stdout_data, _ = proc.communicate(input=stdin_data)
            if proc.returncode != 0:
                raise RuntimeError("git cat-file --batch failed")

            stream = io.BytesIO(stdout_data)
            parsed_count = 0
            for sha, file_path in sha_to_filename.items():
                header = stream.readline()
                if not header:
                    raise RuntimeError(f"early cat-file EOF: expected header for {sha}")
                h_parts = header.split()
                if len(h_parts) < 3:
                    raise RuntimeError(f"git cat-file returned malformed header for {sha}: {header}")
                h_sha = h_parts[0].decode("utf-8")
                h_type = h_parts[1]
                h_size_str = h_parts[2]
                if h_sha != sha:
                    raise RuntimeError(f"git cat-file OID mismatch: expected {sha}, got {h_sha}")
                if h_type != b"blob":
                    raise RuntimeError(f"git cat-file returned invalid type for {sha}: {h_type}")
                try:
                    size = int(h_size_str)
                except ValueError:
                    raise RuntimeError(f"git cat-file returned invalid size for {sha}: {h_size_str}")

                content = stream.read(size)
                if len(content) != size:
                    raise RuntimeError(f"early cat-file EOF: expected {size} bytes for blob {sha}, got {len(content)}")

                terminator = stream.read(1)
                if terminator != b"\n":
                    raise RuntimeError(f"git cat-file missing terminator newline for {sha}, got {terminator}")

                try:
                    snapshot = json.loads(content.decode("utf-8"))
                except Exception as e:
                    raise RuntimeError(f"Malformed JSON in committed snapshot for blob {sha}: {e}")

                if not isinstance(snapshot, dict):
                    raise RuntimeError(f"Committed snapshot for {sha} is not a JSON object")

                basename = os.path.basename(file_path)
                filename_task_id = os.path.splitext(basename)[0]
                validate_archive_snapshot(snapshot, filename_task_id=filename_task_id, outbox_snapshots=outbox_snapshots)

                task_id = normalize_task_id(
                    snapshot.get("task_id")
                    or ((snapshot.get("task") or {}).get("id"))
                    or snapshot.get("id")
                )
                outcome = str(snapshot.get("terminal_outcome") or "").strip().lower() or TERMINAL_OUTCOME_COMPLETED
                archived_at = str(snapshot.get("archived_at") or "").strip()
                summaries.append({
                    "task_id": task_id,
                    "terminal_outcome": outcome,
                    "archived_at": archived_at,
                })
                parsed_count += 1

            # Count validation
            if parsed_count != len(sha_to_filename):
                raise RuntimeError(f"git cat-file count mismatch: expected {len(sha_to_filename)} blobs, parsed {parsed_count}")

    # Process newly created / uncommitted local snapshots
    if allow_uncommitted and ARCHIVE_TASKS_DIR.exists():
        for path in ARCHIVE_TASKS_DIR.glob("*.json"):
            try:
                st = os.lstat(path)
                import stat
                if stat.S_ISLNK(st.st_mode):
                    raise RuntimeError(f"uncommitted snapshot cannot be a symlink: {path}")
                if not stat.S_ISREG(st.st_mode):
                    continue
            except OSError:
                continue
            try:
                path.relative_to(ARCHIVE_TASKS_DIR)
            except ValueError:
                continue

            basename = path.name
            if basename not in committed_snapshots:
                try:
                    text = read_task_archive_file_safe(path).strip()
                    if not text:
                        raise RuntimeError(f"uncommitted snapshot is empty: {path}")
                    snapshot = json.loads(text)
                    filename_task_id = os.path.splitext(basename)[0]
                    validate_archive_snapshot(snapshot, filename_task_id=filename_task_id, outbox_snapshots=outbox_snapshots)

                    task_id = normalize_task_id(
                        snapshot.get("task_id")
                        or ((snapshot.get("task") or {}).get("id"))
                        or snapshot.get("id")
                    )
                    outcome = str(snapshot.get("terminal_outcome") or "").strip().lower() or TERMINAL_OUTCOME_COMPLETED
                    archived_at = str(snapshot.get("archived_at") or "").strip()
                    summaries.append({
                        "task_id": task_id,
                        "terminal_outcome": outcome,
                        "archived_at": archived_at,
                    })
                except Exception as e:
                    raise RuntimeError(f"Failed to parse newly created snapshot at {path}: {e}")

    total_found = len(summaries)
    if existing_total > 0 and total_found < existing_total:
        raise RuntimeError(
            f"Archive index rebuild check failed: found {total_found} snapshots but "
            f"existing index has {existing_total}. Failing closed to prevent index downgrade."
        )

    summaries.sort(key=lambda item: (str(item.get("archived_at") or ""), str(item.get("task_id") or "")), reverse=True)
    index = default_archive_index()
    index["counts"]["total"] = len(summaries)
    index["counts"][TERMINAL_OUTCOME_COMPLETED] = sum(1 for item in summaries if item["terminal_outcome"] == TERMINAL_OUTCOME_COMPLETED)
    index["counts"][TERMINAL_OUTCOME_SUPERSEDED] = sum(1 for item in summaries if item["terminal_outcome"] == TERMINAL_OUTCOME_SUPERSEDED)
    index["recent_terminal_ids"] = [item["task_id"] for item in summaries[: max(0, int(recent_limit))]]
    index["updated_at"] = summaries[0]["archived_at"] if summaries else None
    save_archive_index(index)
    return index


def rebuild_archive_index(*, recent_limit: int = DEFAULT_RECENT_LIMIT) -> dict[str, Any]:
    with canonical_task_state_lock_file(
        STATUS_FILE,
        shared=False,
        nonblocking=False,
    ):
        return _rebuild_archive_index_locked(
            recent_limit=recent_limit,
            allow_uncommitted=True,
            bypass_downgrade_check=False,
        )


def _archive_task_snapshot_locked(
    task: dict[str, Any],
    *,
    handoffs: Iterable[dict[str, Any]] | None = None,
    blockers: Iterable[dict[str, Any]] | None = None,
    archived_at: str | None = None,
    recent_limit: int = DEFAULT_RECENT_LIMIT,
) -> dict[str, Any]:
    if not is_terminal_task(task):
        raise ValueError("Only terminal tasks can be archived")
    task_id = normalize_task_id(task.get("id"))
    if not task_id:
        raise ValueError("Task id is required for archiving")

    path = archive_task_path(task_id)
    if path.is_symlink():
        raise RuntimeError(f"archive-leaf cannot be a symlink: {path}")
    existing = load_archived_snapshot(task_id)
    archived_at = archived_at or (
        str(existing.get("archived_at") or "").strip()
        if isinstance(existing, dict)
        else ""
    ) or iso_now()
    snapshot = {
        "version": ARCHIVE_VERSION,
        "task_id": task_id,
        "archived_at": archived_at,
        "terminal_status": TERMINAL_STATUS_DONE,
        "terminal_outcome": terminal_outcome_for(task) or TERMINAL_OUTCOME_COMPLETED,
        "task": deepcopy(task),
        "handoffs": deepcopy(list(handoffs or [])),
        "blockers": deepcopy(list(blockers or [])),
    }
    if existing:
        if existing != snapshot:
            raise RuntimeError(
                f"existing archive snapshot conflicts with terminal task: {task_id}"
            )
        # An exact snapshot may have survived a crash before the index write.
        _rebuild_archive_index_locked(recent_limit=recent_limit)
        return existing
    write_json(archive_task_path(task_id), snapshot)
    _archive_fault("snapshot")

    index = load_archive_index()
    counts = index.setdefault("counts", {})
    counts["total"] = int(counts.get("total") or 0) + 1
    outcome = snapshot["terminal_outcome"]
    counts[TERMINAL_OUTCOME_COMPLETED] = int(counts.get(TERMINAL_OUTCOME_COMPLETED) or 0)
    counts[TERMINAL_OUTCOME_SUPERSEDED] = int(counts.get(TERMINAL_OUTCOME_SUPERSEDED) or 0)
    if outcome in {TERMINAL_OUTCOME_COMPLETED, TERMINAL_OUTCOME_SUPERSEDED}:
        counts[outcome] += 1
    recent_ids = [task_id]
    recent_ids.extend(item for item in index.get("recent_terminal_ids", []) if normalize_task_id(item) and normalize_task_id(item) != task_id)
    index["recent_terminal_ids"] = recent_ids[: max(0, int(recent_limit))]
    index["updated_at"] = archived_at
    save_archive_index(index)
    _archive_fault("index")
    return snapshot


def archive_task_snapshot(
    task: dict[str, Any],
    *,
    handoffs: Iterable[dict[str, Any]] | None = None,
    blockers: Iterable[dict[str, Any]] | None = None,
    archived_at: str | None = None,
    recent_limit: int = DEFAULT_RECENT_LIMIT,
) -> dict[str, Any]:
    with canonical_task_state_lock_file(
        STATUS_FILE,
        shared=False,
        nonblocking=False,
    ):
        return _archive_task_snapshot_locked(
            task,
            handoffs=handoffs,
            blockers=blockers,
            archived_at=archived_at,
            recent_limit=recent_limit,
        )


class TaskResolver:
    def __init__(
        self,
        active_tasks: Iterable[dict[str, Any]] | dict[str, dict[str, Any]] | None = None,
        *,
        status_root: str | Path | None = None,
        archive_tasks_dir: str | Path | None = None,
        terminal_facts: Mapping[str, Mapping[str, Any]] | None = None,
        allow_archive_lookup: bool = True,
    ) -> None:
        if isinstance(active_tasks, dict):
            self._active = {
                normalize_task_id(task_id): deepcopy(task)
                for task_id, task in active_tasks.items()
                if normalize_task_id(task_id) and isinstance(task, dict)
            }
        else:
            self._active = {
                normalize_task_id(task.get("id")): deepcopy(task)
                for task in (active_tasks or [])
                if isinstance(task, dict) and normalize_task_id(task.get("id"))
            }
        self._terminal_facts = {}
        for task_id, fact in (terminal_facts or {}).items():
            normalized_id = normalize_task_id(task_id)
            if (
                not normalized_id
                or not isinstance(fact, Mapping)
                or str(fact.get("status") or "done").strip().lower() != "done"
                or str(fact.get("terminal_outcome") or "completed").strip().lower()
                not in {TERMINAL_OUTCOME_COMPLETED, TERMINAL_OUTCOME_SUPERSEDED}
            ):
                continue
            record: dict[str, Any] = {
                "id": normalized_id,
                "status": "done",
                "terminal_outcome": str(
                    fact.get("terminal_outcome") or "completed"
                ).strip().lower(),
            }
            if isinstance(fact.get("generation"), int):
                record["generation"] = fact["generation"]
            tracks = compact_completion_tracks(fact.get("completion_tracks"))
            if tracks:
                record["completion_tracks"] = tracks
            self._terminal_facts[normalized_id] = record
        self._allow_archive_lookup = bool(allow_archive_lookup)
        if archive_tasks_dir is not None:
            self._archive_tasks_dir = Path(archive_tasks_dir).expanduser().resolve()
        elif status_root is not None:
            self._archive_tasks_dir = archive_tasks_dir_for_status_root(status_root)
        else:
            self._archive_tasks_dir = None
        self._archive_task_cache: dict[str, dict[str, Any] | None] = {}
        self._archive_snapshot_cache: dict[str, dict[str, Any] | None] = {}

    def active_task_map(self) -> dict[str, dict[str, Any]]:
        return deepcopy(self._active)

    def source(self, task_id: str | None) -> str | None:
        normalized = normalize_task_id(task_id)
        if not normalized:
            return None
        if normalized in self._active:
            return "active"
        if normalized in self._terminal_facts:
            return "terminal_fact"
        if self.get(normalized) is not None:
            return "archive"
        return None

    def get(self, task_id: str | None) -> dict[str, Any] | None:
        normalized = normalize_task_id(task_id)
        if not normalized:
            return None
        active = self._active.get(normalized)
        if active is not None:
            return deepcopy(active)
        fact = self._terminal_facts.get(normalized)
        if fact is not None:
            return deepcopy(fact)
        if not self._allow_archive_lookup:
            return None
        if normalized not in self._archive_task_cache:
            self._archive_task_cache[normalized] = self._load_archived_task(normalized)
        cached = self._archive_task_cache.get(normalized)
        return deepcopy(cached) if isinstance(cached, dict) else None

    def snapshot(self, task_id: str | None) -> dict[str, Any] | None:
        normalized = normalize_task_id(task_id)
        if not normalized or normalized in self._active:
            return None
        if not self._allow_archive_lookup:
            return None
        if normalized not in self._archive_snapshot_cache:
            self._archive_snapshot_cache[normalized] = self._load_archived_snapshot(normalized)
        cached = self._archive_snapshot_cache.get(normalized)
        return deepcopy(cached) if isinstance(cached, dict) else None

    def dependency_satisfied(self, task_id: str | None) -> bool:
        return task_satisfies_dependency(self.get(task_id))

    def dependency_status(self, task_id: str | None) -> str:
        task = self.get(task_id)
        if task is None:
            return "missing"
        status = task_status(task)
        if status == TERMINAL_STATUS_DONE and terminal_outcome_for(task) == TERMINAL_OUTCOME_SUPERSEDED:
            return TERMINAL_OUTCOME_SUPERSEDED
        return status or "missing"

    def _load_archived_snapshot(self, task_id: str | None) -> dict[str, Any] | None:
        if self._archive_tasks_dir is None:
            return load_archived_snapshot(task_id)
        normalized = normalize_task_id(task_id)
        if not normalized:
            return None
        path = archive_task_path_in_dir(normalized, self._archive_tasks_dir)
        try:
            text = read_task_archive_file_safe(path)
            if not text.strip():
                return None
            snapshot = json.loads(text)
        except FileNotFoundError:
            return None
        except Exception as e:
            raise RuntimeError(f"Failed to load archive snapshot safely: {e}")
        return validate_archive_snapshot(snapshot, filename_task_id=normalized)

    def _load_archived_task(self, task_id: str | None) -> dict[str, Any] | None:
        snapshot = self._load_archived_snapshot(task_id)
        if not snapshot:
            return None
        return task_from_archive_snapshot(snapshot)
