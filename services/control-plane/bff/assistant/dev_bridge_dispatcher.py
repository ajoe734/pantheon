"""Dispatcher: verify signed task packet and materialise tasks via scripts/ai_status.py.

ASST-INTEG-006 — owned by Claude2.

Flow:
1. Verify packet signature (HMAC-SHA256 via dev_bridge_signer).
2. Reject duplicate packets via replay protection.
3. For each BridgeTask in the packet, call the installed governed
   scripts/ai_status.py runtime with the central status root and authoritative
   task-state journal binding. The verified repo-local bridge uses the trusted
   Human/Ops mutation identity while preserving the packet actor in the
   structured TASK_METADATA_JSON provenance envelope.
4. Mark packet as seen so replays are rejected in subsequent calls.
5. Return BridgeDispatchResult with per-task records and audit refs.

The dispatcher never shells the VM outside the governed ai_status.py assignment
and read-only task-state verification commands. Web API code must not call
dispatcher functions directly — they are invoked from a trusted internal
service path or a repo-local script, never from a raw HTTP request handler.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import subprocess
import sys
import stat
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Tuple
from urllib.parse import quote

from .dev_bridge_admission import load_admission_record, persist_admission_record
from .dev_bridge_models import (
    BridgeConstraints,
    BridgeDispatchRequest,
    BridgeDispatchResult,
    BridgeTask,
    DevTaskPacket,
    TaskDispatchRecord,
)
from .dev_bridge_signer import (
    mark_packet_seen,
    packet_digest,
    packet_replay_lock,
    replay_record,
    verify_packet,
)

BRIDGE_STATUS_ACTOR = "Human/Ops"
STATUS_ROOT_ENV = "PANTHEON_STATUS_ROOT"
COMMAND_ROOT_ENV = "PANTHEON_COMMAND_ROOT"
COMMAND_SHA_ENV = "PANTHEON_COMMAND_RUNTIME_SHA"
COMMAND_REMOTE_ENV = "PANTHEON_COMMAND_REMOTE"
COMMAND_BASE_REF_ENV = "PANTHEON_COMMAND_BASE_REF"
TASK_STATE_MODE_ENV = "PANTHEON_TASK_STATE_STORE_MODE"
TASK_STATE_EVENT_LOG_ENV = "PANTHEON_TASK_STATE_EVENT_LOG"
REQUIRE_TASK_STATE_READBACK_ENV = (
    "PANTHEON_ASSISTANT_DEV_BRIDGE_REQUIRE_TASK_STATE_READBACK"
)
LEGACY_COMMAND_ENV_NAMES = (
    "PANTHEON_STATUS_COMMAND_ROOT",
    "PANTHEON_STATUS_COMMAND_SHA",
    "PANTHEON_STATUS_COMMAND_REMOTE",
    "PANTHEON_STATUS_COMMAND_BASE_REF",
)
AUTO_WORKER_ENV_NAMES = (
    "ORCH_RUN_ID",
    "ORCH_TASK_ID",
    "ORCH_AGENT_ID",
    "ORCH_PROVIDER",
    "ORCH_SESSION_ID",
    "PANTHEON_WORKTREE_ROOT",
    "ORCH_WORKSPACE_PATH",
    "ORCH_RUNNER_STATUS_PATH",
    "ORCH_HEARTBEAT_PATH",
)
ASSIGN_TIMEOUT_ENV = "PANTHEON_ASSISTANT_DEV_BRIDGE_ASSIGN_TIMEOUT_SECONDS"
DISPATCH_CLAIM_TTL_ENV = "PANTHEON_ASSISTANT_DEV_BRIDGE_CLAIM_TTL_SECONDS"
DEFAULT_ASSIGN_TIMEOUT_SECONDS = 10.0
MAX_ASSIGN_TIMEOUT_SECONDS = 10.0
DEFAULT_DISPATCH_CLAIM_TTL_SECONDS = 300.0
DISPATCH_CLAIM_SCHEMA = "pantheon.assistant-dev-bridge-dispatch-claim.v1"


class MaterializedTaskMissingError(ValueError):
    """The governed task store authoritatively reports that an id is absent."""


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _bounded_seconds(
    environment: Mapping[str, str],
    name: str,
    default: float,
    *,
    minimum: float,
    maximum: float,
) -> float:
    try:
        configured = float(str(environment.get(name) or default))
    except (TypeError, ValueError):
        configured = default
    return min(maximum, max(minimum, configured))


def _assign_timeout_seconds(environment: Mapping[str, str]) -> float:
    return _bounded_seconds(
        environment,
        ASSIGN_TIMEOUT_ENV,
        DEFAULT_ASSIGN_TIMEOUT_SECONDS,
        minimum=0.1,
        maximum=MAX_ASSIGN_TIMEOUT_SECONDS,
    )


def _dispatch_claim_ttl_seconds(environment: Mapping[str, str]) -> float:
    return _bounded_seconds(
        environment,
        DISPATCH_CLAIM_TTL_ENV,
        DEFAULT_DISPATCH_CLAIM_TTL_SECONDS,
        minimum=5.0,
        maximum=3600.0,
    )


def _dispatch_claim_path(repo_root: str, packet_id: str) -> Path:
    identity = hashlib.sha256(packet_id.encode("utf-8")).hexdigest()
    return (
        Path(repo_root)
        / ".orchestrator"
        / "assistant-dev-packet-claims"
        / f"{identity}.json"
    )


def _dispatch_fence_path(repo_root: str, packet_id: str) -> Path:
    identity = hashlib.sha256(packet_id.encode("utf-8")).hexdigest()
    return (
        Path(repo_root)
        / ".orchestrator"
        / "assistant-dev-packet-claims"
        / f"{identity}.lock"
    )


def _open_regular_fence_file(
    root: Path,
    directory_components: Tuple[str, ...],
    filename: str,
    *,
    description: str,
) -> int:
    """Open one fence without following managed parent or leaf symlinks."""

    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptors: List[int] = []
    try:
        descriptor = os.open(root, directory_flags)
        descriptors.append(descriptor)
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise ValueError(f"{description} root is not a directory: {root}")

        for component in directory_components:
            if component in {"", ".", ".."} or "/" in component:
                raise ValueError(
                    f"{description} has an unsafe directory component: {component!r}"
                )
            try:
                child = os.open(component, directory_flags, dir_fd=descriptor)
            except FileNotFoundError:
                try:
                    os.mkdir(component, mode=0o775, dir_fd=descriptor)
                except FileExistsError:
                    pass
                child = os.open(component, directory_flags, dir_fd=descriptor)
            if not stat.S_ISDIR(os.fstat(child).st_mode):
                os.close(child)
                raise ValueError(
                    f"{description} parent is not a directory: {component}"
                )
            descriptors.append(child)
            descriptor = child

        flags = (
            os.O_RDWR
            | os.O_CREAT
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        fence = os.open(filename, flags, 0o600, dir_fd=descriptor)
        if not stat.S_ISREG(os.fstat(fence).st_mode):
            os.close(fence)
            raise ValueError(f"{description} is not a regular file: {filename}")
        return fence
    except OSError as exc:
        raise ValueError(f"{description} is unsafe under {root}") from exc
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _try_acquire_dispatch_fence(repo_root: str, packet_id: str) -> int | None:
    """Hold a crash-released per-packet OS fence across governed work.

    JSON claim expiry is recovery metadata, not permission to overlap a live
    claimant.  The fence is not used by ai_status and therefore cannot recreate
    the parent/child lock cycle repaired by this task.
    """

    path = _dispatch_fence_path(repo_root, packet_id)
    descriptor = _open_regular_fence_file(
        Path(repo_root),
        (".orchestrator", "assistant-dev-packet-claims"),
        path.name,
        description="Bridge dispatch fence",
    )
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(descriptor)
            return None
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _release_dispatch_fence(descriptor: int) -> None:
    try:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def _write_json_atomic(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=str(path.parent),
            delete=False,
        ) as handle:
            json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        os.replace(temporary, path)
        temporary = None
        try:
            directory_fd = os.open(
                path.parent,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
            )
        except OSError:
            return
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _load_dispatch_claim(path: Path) -> Dict[str, object] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Bridge dispatch claim is unreadable: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Bridge dispatch claim must be an object: {path}")
    return payload


def _parse_utc_timestamp(value: object) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _claim_identity(packet: DevTaskPacket, digest: str) -> Dict[str, object]:
    return {
        "packet_id": packet.packet_id,
        "packet_digest": digest,
        "tasks": [
            {
                "task_id": task.id,
                "task_spec_hash": _task_spec_hash(task),
            }
            for task in packet.tasks
        ],
    }


def _claim_packet_dispatch_locked(
    packet: DevTaskPacket,
    *,
    repo_root: str,
    digest: str,
    environment: Mapping[str, str],
) -> tuple[str, Dict[str, object]]:
    """Claim one exact packet without holding the replay lock during shell work."""

    replay = replay_record(packet.packet_id, repo_root=repo_root, lock_held=True)
    if replay is not None:
        return "replay", replay

    path = _dispatch_claim_path(repo_root, packet.packet_id)
    existing = _load_dispatch_claim(path)
    expected_identity = _claim_identity(packet, digest)
    now = datetime.now(timezone.utc)
    if existing is not None:
        observed_identity = {
            key: existing.get(key)
            for key in ("packet_id", "packet_digest", "tasks")
        }
        if observed_identity != expected_identity:
            raise ValueError(
                f"Packet id {packet.packet_id!r} has a mismatched dispatch claim"
            )
        expires_at = _parse_utc_timestamp(existing.get("expires_at"))
        if expires_at is None:
            raise ValueError(
                f"Packet id {packet.packet_id!r} has an invalid dispatch claim expiry"
            )
        if expires_at > now:
            return "busy", existing

    token = os.urandom(24).hex()
    ttl_seconds = _dispatch_claim_ttl_seconds(environment)
    claim = {
        "schema": DISPATCH_CLAIM_SCHEMA,
        **expected_identity,
        "claim_token": token,
        "claimed_at": now.isoformat().replace("+00:00", "Z"),
        "expires_at": datetime.fromtimestamp(
            now.timestamp() + ttl_seconds,
            tz=timezone.utc,
        ).isoformat().replace("+00:00", "Z"),
        "owner_pid": os.getpid(),
    }
    _write_json_atomic(path, claim)
    return "claimed", claim


def _remove_dispatch_claim_locked(
    *,
    repo_root: str,
    packet_id: str,
    claim_token: str,
) -> bool:
    path = _dispatch_claim_path(repo_root, packet_id)
    claim = _load_dispatch_claim(path)
    if claim is None or str(claim.get("claim_token") or "") != claim_token:
        return False
    path.unlink()
    return True


def _release_dispatch_claim(
    *,
    repo_root: str,
    packet_id: str,
    claim_token: str | None,
) -> None:
    if not claim_token:
        return
    with packet_replay_lock(repo_root=repo_root):
        _remove_dispatch_claim_locked(
            repo_root=repo_root,
            packet_id=packet_id,
            claim_token=claim_token,
        )


def _find_repo_root(start: Optional[str] = None) -> str:
    """Walk up from *start* to find the Pantheon repo root."""
    if start:
        candidate = Path(start)
    else:
        env = os.environ.get("PANTHEON_STATUS_ROOT")
        candidate = Path(env) if env else Path(__file__).resolve()
    candidate = candidate if candidate.is_dir() else candidate.parent
    for _ in range(12):
        if (candidate / "ai-status.json").exists():
            return str(candidate)
        parent = candidate.parent
        if parent == candidate:
            break
        candidate = parent
    return start or str(Path.cwd())


def _ai_status_py(repo_root: str) -> str:
    return str(Path(repo_root) / "scripts" / "ai_status.py")


def _same_path(left: str | Path, right: str | Path) -> bool:
    try:
        return Path(left).expanduser().resolve() == Path(right).expanduser().resolve()
    except (OSError, RuntimeError):
        return False


def _merged_environment(
    overrides: Optional[Mapping[str, str]] = None,
) -> Dict[str, str]:
    environment = {**os.environ}
    if overrides:
        environment.update(
            {
                str(name): str(value)
                for name, value in overrides.items()
                if value is not None
            }
        )
    return environment


def _environment_targets_status_root(
    repo_root: str,
    *,
    environment: Optional[Mapping[str, str]] = None,
) -> bool:
    source = environment if environment is not None else os.environ
    raw_status_root = str(source.get(STATUS_ROOT_ENV) or "").strip()
    return bool(raw_status_root) and _same_path(raw_status_root, repo_root)


def _absolute_runtime_path(raw_path: str, *, label: str) -> str:
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        raise RuntimeError(f"{label} must be an absolute path")
    if ".." in path.parts:
        raise RuntimeError(f"{label} cannot contain parent directory references")
    cursor = Path(path.anchor)
    for part in path.parts[1:]:
        cursor = cursor / part
        try:
            if cursor.is_symlink():
                raise RuntimeError(
                    f"{label} cannot include a symlink component: {cursor}"
                )
            if not cursor.exists():
                break
        except OSError as exc:
            raise RuntimeError(f"could not validate {label}: {cursor}") from exc
    return str(path.resolve())


def _code_repo_root() -> Path:
    candidate = Path(__file__).resolve()
    for parent in candidate.parents:
        if (
            (parent / ".git").exists()
            and (parent / "scripts" / "ai_status.py").is_file()
        ):
            return parent
    raise RuntimeError("could not resolve installed dev bridge command root")


def _git_stdout(repo_root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "git command failed").strip()
        raise RuntimeError(detail)
    return result.stdout.strip()


def _runtime_task_state_env(
    repo_root: str,
    *,
    environment: Optional[Mapping[str, str]] = None,
) -> Dict[str, str]:
    """Resolve the live journal binding without trusting a repo template path."""

    source = environment if environment is not None else os.environ
    if _environment_targets_status_root(repo_root, environment=source):
        mode = str(source.get(TASK_STATE_MODE_ENV) or "").strip().lower()
        event_log = str(source.get(TASK_STATE_EVENT_LOG_ENV) or "").strip()
        if mode or event_log:
            if mode not in {"shadow", "authoritative"}:
                raise RuntimeError(
                    f"{TASK_STATE_MODE_ENV} must be shadow or authoritative"
                )
            if not event_log:
                raise RuntimeError(
                    f"{TASK_STATE_EVENT_LOG_ENV} is required in {mode} mode"
                )
            return {
                TASK_STATE_MODE_ENV: mode,
                TASK_STATE_EVENT_LOG_ENV: _absolute_runtime_path(
                    event_log,
                    label=TASK_STATE_EVENT_LOG_ENV,
                ),
            }

    runtime_state_path = Path(repo_root) / ".orchestrator" / "state.json"
    if not runtime_state_path.is_file():
        return {}
    try:
        runtime_state = json.loads(runtime_state_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"could not read supervisor runtime state: {runtime_state_path}"
        ) from exc
    if not isinstance(runtime_state, dict):
        raise RuntimeError("supervisor runtime state must be a JSON object")
    supervisor = runtime_state.get("supervisor")
    shadow = (
        supervisor.get("task_state_shadow")
        if isinstance(supervisor, dict)
        else None
    )
    if not isinstance(shadow, dict):
        return {}
    mode = str(shadow.get("mode") or "").strip().lower()
    event_log = str(shadow.get("event_log") or "").strip()
    if mode not in {"shadow", "authoritative"}:
        return {}
    if not event_log:
        raise RuntimeError(
            "supervisor task-state runtime binding requires an absolute event log"
        )
    return {
        TASK_STATE_MODE_ENV: mode,
        TASK_STATE_EVENT_LOG_ENV: _absolute_runtime_path(
            event_log,
            label="supervisor task-state event log",
        ),
    }


def _governed_command_root(
    repo_root: str,
    *,
    task_state_env: Dict[str, str],
    environment: Optional[Mapping[str, str]] = None,
) -> Optional[Path]:
    source = environment if environment is not None else os.environ
    if _environment_targets_status_root(repo_root, environment=source):
        configured = str(source.get(COMMAND_ROOT_ENV) or "").strip()
        if configured:
            path = Path(configured).expanduser()
            if not path.is_absolute():
                raise RuntimeError(f"{COMMAND_ROOT_ENV} must be an absolute path")
            return path.resolve()

    if task_state_env:
        code_root = _code_repo_root()
        if not _same_path(code_root, repo_root):
            return code_root
        raise RuntimeError(
            "authoritative dev bridge dispatch requires an installed command "
            "runtime distinct from the central status root"
        )
    return None


def _status_command_context(
    repo_root: str,
    *,
    environment: Optional[Mapping[str, str]] = None,
) -> Tuple[str, Dict[str, str], bool]:
    """Return executable/env for either a governed runtime or legacy fixture."""

    source = environment if environment is not None else os.environ
    task_state_env = _runtime_task_state_env(
        repo_root,
        environment=source,
    )
    command_root = _governed_command_root(
        repo_root,
        task_state_env=task_state_env,
        environment=source,
    )
    if command_root is None:
        return (
            _ai_status_py(repo_root),
            {STATUS_ROOT_ENV: str(Path(repo_root).resolve())},
            False,
        )

    ai_status = command_root / "scripts" / "ai_status.py"
    if not ai_status.is_file():
        raise RuntimeError(
            f"installed command runtime is missing scripts/ai_status.py: {command_root}"
        )

    if _environment_targets_status_root(repo_root, environment=source) and str(
        source.get(COMMAND_ROOT_ENV) or ""
    ).strip():
        command_sha = str(source.get(COMMAND_SHA_ENV) or "").strip()
        if not command_sha:
            raise RuntimeError(
                f"{COMMAND_SHA_ENV} is required with {COMMAND_ROOT_ENV}"
            )
        command_remote = str(
            source.get(COMMAND_REMOTE_ENV) or "ajoe734/pantheon"
        ).strip()
        command_base_ref = str(
            source.get(COMMAND_BASE_REF_ENV) or "origin/dev"
        ).strip()
    else:
        command_sha = _git_stdout(command_root, "rev-parse", "HEAD")
        command_remote = _git_stdout(command_root, "remote", "get-url", "origin")
        command_base_ref = "origin/dev"

    env = {
        STATUS_ROOT_ENV: str(Path(repo_root).resolve()),
        COMMAND_ROOT_ENV: str(command_root),
        COMMAND_SHA_ENV: command_sha,
        COMMAND_REMOTE_ENV: command_remote,
        COMMAND_BASE_REF_ENV: command_base_ref,
        **task_state_env,
    }
    return str(ai_status), env, True


def _configured_allowed_repos() -> set[str]:
    configured = str(
        os.environ.get("PANTHEON_ASSISTANT_DEV_BRIDGE_ALLOWED_REPOS") or "pantheon"
    )
    return {item.strip() for item in configured.split(",") if item.strip()}


# ---------------------------------------------------------------------------
# Constraint enforcement
# ---------------------------------------------------------------------------

def _check_constraints(packet: DevTaskPacket) -> List[str]:
    """Return a list of constraint violation messages (empty = OK)."""
    c: BridgeConstraints = packet.constraints
    violations: List[str] = []
    if not c.no_direct_shell_from_web:
        violations.append(
            "Packet constraint noDirectShellFromWeb is False — "
            "this dispatcher requires it to be True"
        )
    if not c.requires_branch_pr_merge:
        violations.append(
            "Packet constraint requiresBranchPrMerge is False — task branches and reviewed PR merge are required"
        )
    requested_repos = {
        str(item or "").strip()
        for item in c.allowed_repos
        if str(item or "").strip()
    }
    configured_repos = _configured_allowed_repos()
    if not requested_repos:
        violations.append("Packet constraint allowedRepos must not be empty")
    if "pantheon" not in requested_repos:
        violations.append(
            f"Packet constraint allowedRepos={c.allowed_repos!r} does not include 'pantheon'"
        )
    unconfigured = sorted(requested_repos - configured_repos)
    if unconfigured:
        violations.append(
            "Packet constraint allowedRepos contains unconfigured repositories: "
            + ", ".join(unconfigured)
        )
    return violations


def _task_spec(task: BridgeTask) -> Dict[str, object]:
    return {
        "id": task.id,
        "title": task.title,
        "owner": task.owner,
        "reviewer": task.reviewer,
        "phase": task.phase,
        "depends_on": list(task.depends_on),
        "artifacts": list(task.artifacts),
        "acceptance": list(task.acceptance),
        "summary": task.summary,
    }


def _task_spec_hash(task: BridgeTask) -> str:
    encoded = json.dumps(
        _task_spec(task),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _task_metadata(packet: DevTaskPacket, task: BridgeTask) -> Dict[str, object]:
    return {
        "dev_bridge": {
            "packet_id": packet.packet_id,
            "packet_digest": packet_digest(packet),
            "task_spec_hash": _task_spec_hash(task),
            "task_spec": _task_spec(task),
            "conversation_id": packet.source_conversation_id,
            "source_turn_ids": list(packet.source_turn_ids),
            "documents": [
                document.model_dump(mode="json", by_alias=True)
                for document in packet.documents
            ],
            "audit_conversation_href": packet.audit_conversation_href,
            "emitted_at": packet.emitted_at,
            "intent": packet.intent,
            "mode": packet.mode,
            "actor": packet.actor.model_dump(mode="json", by_alias=True),
        }
    }


def _audit_refs(packet: DevTaskPacket, dispatched_at: str) -> Dict[str, object]:
    return {
        "packetId": packet.packet_id,
        "packetDigest": packet_digest(packet),
        "conversationId": packet.source_conversation_id,
        "sourceTurnIds": packet.source_turn_ids,
        "documents": [d.path for d in packet.documents],
        "taskIds": [t.id for t in packet.tasks],
        "auditConversationHref": packet.audit_conversation_href,
        "dispatchedAt": dispatched_at,
    }


def _admission_tasks(packet: DevTaskPacket) -> List[Dict[str, object]]:
    return [
        {
            "task_id": task.id,
            "task_spec_hash": _task_spec_hash(task),
            "task_spec": _task_spec(task),
        }
        for task in packet.tasks
    ]


def _admission_provenance(packet: DevTaskPacket) -> Dict[str, object]:
    return {
        "packet_version": packet.version,
        "actor": packet.actor.model_dump(mode="json", by_alias=True),
        "mode": packet.mode,
        "intent": packet.intent,
        "conversation_id": packet.source_conversation_id,
        "source_turn_ids": list(packet.source_turn_ids),
        "documents": [
            document.model_dump(mode="json", by_alias=True)
            for document in packet.documents
        ],
        "audit_conversation_href": packet.audit_conversation_href,
        "emitted_at": packet.emitted_at,
        "constraints": packet.constraints.model_dump(mode="json", by_alias=True),
        "tasks": _admission_tasks(packet),
    }


def _materialized_task_candidates(*, repo_root: str, task_id: str) -> List[Dict[str, object]]:
    candidates: List[Dict[str, object]] = []
    status_path = Path(repo_root) / "ai-status.json"
    try:
        status_payload = json.loads(status_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError) as exc:
        raise OSError(f"could not read active ai-status state: {status_path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"active ai-status state is invalid JSON: {status_path}") from exc
    if not isinstance(status_payload, dict):
        raise ValueError("active ai-status state must be an object")
    tasks = status_payload.get("tasks", [])
    if tasks is not None and not isinstance(tasks, list):
        raise ValueError("active ai-status tasks must be a list")
    for item in tasks or []:
        if isinstance(item, dict) and str(item.get("id") or "") == task_id:
            candidates.append(item)

    archive_name = quote(task_id, safe="-_.") + ".json"
    archive_path = Path(repo_root) / "ai-task-archive" / "tasks" / archive_name
    if archive_path.exists():
        try:
            archive = json.loads(archive_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError) as exc:
            raise OSError(f"could not read terminal task snapshot: {archive_path}") from exc
        except json.JSONDecodeError as exc:
            raise ValueError(f"terminal task snapshot is invalid JSON: {archive_path}") from exc
        task = archive.get("task") if isinstance(archive, dict) else None
        if not isinstance(task, dict):
            raise ValueError(f"terminal task snapshot is invalid: {archive_path}")
        candidates.append(task)
    return candidates


def _validate_materialized_task_candidate(
    packet: DevTaskPacket,
    task: BridgeTask,
    candidate: Mapping[str, object],
) -> None:
    for field in ("depends_on", "artifacts", "acceptance"):
        value = candidate.get(field)
        if not isinstance(value, list) or any(
            not isinstance(item, str) for item in value
        ):
            raise ValueError(
                f"materialized task {task.id!r} has invalid {field} provenance"
            )
    observed_spec = {
        "id": candidate.get("id"),
        "title": candidate.get("title"),
        "owner": candidate.get("owner"),
        "reviewer": candidate.get("reviewer"),
        "phase": candidate.get("phase"),
        "depends_on": list(candidate["depends_on"]),
        "artifacts": list(candidate["artifacts"]),
        "acceptance": list(candidate["acceptance"]),
        "summary": candidate.get("summary_zh"),
    }
    if observed_spec != _task_spec(task):
        raise ValueError(
            f"materialized task {task.id!r} does not match the signed task spec"
        )
    if candidate.get("dev_bridge") != _task_metadata(packet, task)["dev_bridge"]:
        raise ValueError(
            f"materialized task {task.id!r} does not match signed bridge provenance"
        )


def _run_readback_command(
    command: List[str],
    *,
    environment: Mapping[str, str],
    repo_root: str,
    label: str,
) -> Dict[str, object]:
    timeout_seconds = _assign_timeout_seconds(environment)
    try:
        result = subprocess.run(
            command,
            env=dict(environment),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            cwd=repo_root,
        )
    except subprocess.TimeoutExpired as exc:
        raise OSError(f"{label} timed out after {timeout_seconds:g}s") from exc
    except OSError as exc:
        raise OSError(f"{label} could not execute: {exc}") from exc

    output = (result.stdout or "").strip()
    error = (result.stderr or "").strip()
    if result.returncode != 0:
        detail = error or output or f"exit {result.returncode}"
        if result.returncode in {3, 75}:
            raise OSError(f"{label} unavailable: {detail[:500]}")
        raise ValueError(f"{label} failed: {detail[:500]}")
    try:
        payload = json.loads(output)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must return a JSON object")
    return payload


def _run_governed_task_show(
    *,
    ai_status: str,
    task_id: str,
    environment: Mapping[str, str],
    repo_root: str,
) -> Dict[str, object]:
    """Read one task through the governed command and classify exact absence.

    `show` uses exit 1 for an unknown task.  Only its exact, id-bound error is
    treated as an absent row; every other nonzero exit, malformed response, or
    runtime failure remains fail-closed.
    """

    timeout_seconds = _assign_timeout_seconds(environment)
    label = f"canonical task-state readback for {task_id}"
    command = [sys.executable, ai_status, "show", task_id]
    try:
        result = subprocess.run(
            command,
            env=dict(environment),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            cwd=repo_root,
        )
    except subprocess.TimeoutExpired as exc:
        raise OSError(f"{label} timed out after {timeout_seconds:g}s") from exc
    except OSError as exc:
        raise OSError(f"{label} could not execute: {exc}") from exc

    output = (result.stdout or "").strip()
    error = (result.stderr or "").strip()
    if result.returncode != 0:
        if (
            result.returncode == 1
            and not output
            and error == f"Unknown task: {task_id}"
        ):
            raise MaterializedTaskMissingError(
                f"materialized task {task_id!r} is missing"
            )
        detail = error or output or f"exit {result.returncode}"
        if result.returncode in {3, 75}:
            raise OSError(f"{label} unavailable: {detail[:500]}")
        raise ValueError(f"{label} failed: {detail[:500]}")
    try:
        payload = json.loads(output)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must return a JSON object")
    return payload


def _canonical_task_state_readback(
    packet: DevTaskPacket,
    *,
    repo_root: str,
    environment: Mapping[str, str],
    tasks: Optional[List[BridgeTask]] = None,
) -> Dict[str, object]:
    task_state_env = _runtime_task_state_env(
        repo_root,
        environment=environment,
    )
    required = str(
        environment.get(REQUIRE_TASK_STATE_READBACK_ENV) or ""
    ).strip().lower() in {"1", "true", "yes", "on"}
    if not task_state_env:
        if required:
            raise ValueError(
                "canonical task-state runtime binding is missing; "
                "file/activity-only bridge dispatch is not admissible"
            )
        return {
            "status": "legacy_projection_only",
            "taskIds": [task.id for task in packet.tasks],
        }

    ai_status, status_env, governed = _status_command_context(
        repo_root,
        environment=environment,
    )
    if not governed:
        raise ValueError(
            "canonical task-state readback requires the governed command runtime"
        )
    command_environment = {**environment, **status_env}
    for name in AUTO_WORKER_ENV_NAMES:
        command_environment.pop(name, None)
    command_environment["AI_NAME"] = BRIDGE_STATUS_ACTOR

    selected_tasks = list(tasks) if tasks is not None else list(packet.tasks)
    task_readbacks: List[Dict[str, object]] = []
    for task in selected_tasks:
        payload = _run_governed_task_show(
            ai_status=ai_status,
            task_id=task.id,
            environment=command_environment,
            repo_root=repo_root,
        )
        source = str(payload.get("source") or "").strip()
        if source == "active":
            candidate = payload.get("task")
        elif source == "archive":
            snapshot = payload.get("snapshot")
            candidate = snapshot.get("task") if isinstance(snapshot, dict) else None
        else:
            candidate = None
        if not isinstance(candidate, dict):
            raise ValueError(
                f"canonical task-state readback for {task.id} has no task row"
            )
        _validate_materialized_task_candidate(packet, task, candidate)
        task_readbacks.append(
            {
                "taskId": task.id,
                "source": source,
                "taskSpecHash": _task_spec_hash(task),
            }
        )

    verify_script = Path(ai_status).with_name("verify_task_state_store.py")
    if not verify_script.is_file():
        raise ValueError(
            "governed command runtime is missing scripts/verify_task_state_store.py"
        )
    projection = _run_readback_command(
        [
            sys.executable,
            str(verify_script),
            "--event-log",
            task_state_env[TASK_STATE_EVENT_LOG_ENV],
            "--status-file",
            str(Path(repo_root) / "ai-status.json"),
            "--json",
        ],
        environment=command_environment,
        repo_root=repo_root,
        label="canonical task-state journal/projection readback",
    )
    if projection.get("ok") is not True:
        raise ValueError(
            "canonical task-state journal/projection readback is not at parity"
        )
    return {
        "status": "verified",
        "storeMode": task_state_env[TASK_STATE_MODE_ENV],
        "eventLog": task_state_env[TASK_STATE_EVENT_LOG_ENV],
        "taskIds": [task.id for task in selected_tasks],
        "tasks": task_readbacks,
        "checkpoint": {
            "eventCount": projection.get("event_count"),
            "lastEventId": projection.get("last_event_id"),
            "expectedStateSha256": projection.get("expected_state_sha256"),
            "projectedStateSha256": projection.get("projected_state_sha256"),
        },
    }


def _validate_materialized_tasks(
    packet: DevTaskPacket,
    *,
    repo_root: str,
    environment: Optional[Mapping[str, str]] = None,
) -> Dict[str, object]:
    """Bind a successful dispatch/admission to canonical task-state readback."""

    command_environment = _merged_environment(environment)
    task_state_env = _runtime_task_state_env(
        repo_root,
        environment=command_environment,
    )
    if task_state_env:
        # Governed mode has exactly one read authority.  Repository-local
        # projection and archive files are derived outputs and must never be a
        # fallback when the authoritative `show` path is unavailable.
        return _canonical_task_state_readback(
            packet,
            repo_root=repo_root,
            environment=command_environment,
        )

    for task in packet.tasks:
        candidates = _materialized_task_candidates(
            repo_root=repo_root,
            task_id=task.id,
        )
        if not candidates:
            raise ValueError(f"materialized task {task.id!r} is missing")
        for candidate in candidates:
            _validate_materialized_task_candidate(packet, task, candidate)
    return _canonical_task_state_readback(
        packet,
        repo_root=repo_root,
        environment=command_environment,
    )


# ---------------------------------------------------------------------------
# Per-task dispatch
# ---------------------------------------------------------------------------

def _dispatch_task(
    task: BridgeTask,
    *,
    packet: DevTaskPacket,
    repo_root: str,
    dry_run: bool,
    environment: Optional[Mapping[str, str]] = None,
) -> TaskDispatchRecord:
    """Call the governed status runtime for a single verified bridge task.

    Returns a TaskDispatchRecord indicating success or failure.
    Does NOT raise — errors are captured in the record.
    """
    record = TaskDispatchRecord(
        taskId=task.id,
        owner=task.owner,
        reviewer=task.reviewer,
        status="dry_run" if dry_run else "dispatched",
    )

    if dry_run:
        return record

    try:
        ai_status, status_env, governed = _status_command_context(
            repo_root,
            environment=environment,
        )
    except RuntimeError as exc:
        record.status = "error"
        record.error = str(exc)
        return record
    if not Path(ai_status).exists():
        record.status = "error"
        record.error = f"scripts/ai_status.py not found at {ai_status!r}"
        return record

    env = _merged_environment(environment)
    if not governed:
        for name in (
            COMMAND_ROOT_ENV,
            COMMAND_SHA_ENV,
            COMMAND_REMOTE_ENV,
            COMMAND_BASE_REF_ENV,
            TASK_STATE_MODE_ENV,
            TASK_STATE_EVENT_LOG_ENV,
            *LEGACY_COMMAND_ENV_NAMES,
        ):
            env.pop(name, None)
    env.update(status_env)
    # Signature verification and constraint checks happen before this private
    # helper is reached. Run the canonical mutation as the repo-local bridge
    # service rather than borrowing any ambient auto-worker lease. The signed
    # packet actor remains immutable in TASK_METADATA_JSON and admission
    # evidence, while direct worker status commands still pass through the
    # normal lease gate in scripts/ai_status.py.
    for name in AUTO_WORKER_ENV_NAMES:
        env.pop(name, None)
    env["AI_NAME"] = BRIDGE_STATUS_ACTOR
    env["TASK_METADATA_JSON"] = json.dumps(
        _task_metadata(packet, task),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )

    # A previous exact attempt may have materialised this row before the
    # parent observed a timeout/nonzero exit.  In governed mode, only the
    # authoritative `show` command and journal parity check may establish that
    # idempotent prefix; repository-local task files are never consulted.
    if governed:
        try:
            _canonical_task_state_readback(
                packet,
                repo_root=repo_root,
                environment=env,
                tasks=[task],
            )
        except MaterializedTaskMissingError:
            pass
        except OSError as exc:
            record.status = "retryable"
            record.error = f"existing bridge task readback unavailable: {exc}"
            return record
        except ValueError as exc:
            record.status = "error"
            record.error = str(exc)
            return record
        else:
            return record
    else:
        try:
            existing_candidates = _materialized_task_candidates(
                repo_root=repo_root,
                task_id=task.id,
            )
            for candidate in existing_candidates:
                _validate_materialized_task_candidate(packet, task, candidate)
        except OSError as exc:
            record.status = "retryable"
            record.error = f"existing bridge task readback unavailable: {exc}"
            return record
        except ValueError as exc:
            record.status = "error"
            record.error = str(exc)
            return record
        if existing_candidates:
            return record

    cmd = [
        sys.executable,
        ai_status,
        "assign",
        task.id,
        task.owner,
        task.reviewer,
        task.title,
    ]

    timeout_seconds = _assign_timeout_seconds(env)
    failure_status: str | None = None
    failure_error: str | None = None
    try:
        result = subprocess.run(
            cmd,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            cwd=repo_root,
        )
        if result.returncode != 0:
            failure_status = "retryable" if result.returncode == 75 else "error"
            failure_error = (
                result.stderr or result.stdout or "non-zero exit"
            ).strip()[:2000]
    except subprocess.TimeoutExpired:
        failure_status = "retryable"
        failure_error = f"ai_status.py assign timed out after {timeout_seconds:g}s"
    except OSError as exc:
        failure_status = "error"
        failure_error = str(exc)

    if failure_error is None:
        return record

    if governed:
        # A nonzero exit or timeout is not proof that the authoritative writer
        # failed before commit.  Read the exact row back immediately and accept
        # only complete signed spec/provenance plus journal parity.  Missing,
        # unavailable, malformed, or mismatched readback never reaches
        # admission and never falls back to local projections.
        try:
            _canonical_task_state_readback(
                packet,
                repo_root=repo_root,
                environment=env,
                tasks=[task],
            )
        except MaterializedTaskMissingError:
            record.status = failure_status or "error"
            record.error = failure_error
        except OSError as exc:
            record.status = "retryable"
            record.error = (
                f"{failure_error}; authoritative post-assign readback unavailable: {exc}"
            )
        except ValueError as exc:
            record.status = "error"
            record.error = (
                f"{failure_error}; authoritative post-assign readback invalid: {exc}"
            )
        else:
            return record
    else:
        record.status = failure_status or "error"
        record.error = failure_error

    return record


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def _replay_dispatch_result(
    packet: DevTaskPacket,
    *,
    repo_root: str,
    digest: str,
    replay: Mapping[str, object],
    dispatched_at: str,
    dry_run: bool,
    environment: Mapping[str, str],
    audit_refs: Dict[str, object],
) -> BridgeDispatchResult:
    """Validate a terminal replay without holding the replay claim lock."""

    recorded_digest = str(replay.get("digest") or "").strip() or None
    if recorded_digest and recorded_digest != digest:
        raise ValueError(
            f"Packet id {packet.packet_id!r} is already bound to a different payload"
        )
    admission_record = None
    replay_errors: List[str] = []
    replay_retryable = False
    if recorded_digest:
        try:
            admission_record = load_admission_record(
                repo_root=repo_root,
                packet_id=packet.packet_id,
                packet_digest=digest,
                expected_provenance=_admission_provenance(packet),
            )
        except (OSError, ValueError) as exc:
            replay_errors.append(f"bridge admission replay validation: {exc}")
            admission_status = "invalid_replay_admission"
        else:
            if admission_record is None:
                replay_errors.append(
                    "bridge admission replay validation: durable admission record is missing"
                )
                admission_status = "missing_replay_admission"
            else:
                started = time.monotonic()
                try:
                    readback = _validate_materialized_tasks(
                        packet,
                        repo_root=repo_root,
                        environment=environment,
                    )
                except OSError as exc:
                    replay_errors.append(
                        f"bridge materialization replay validation: {exc}"
                    )
                    admission_status = "materialization_read_retryable"
                    replay_retryable = True
                except ValueError as exc:
                    replay_errors.append(
                        f"bridge materialization replay validation: {exc}"
                    )
                    admission_status = "invalid_replay_materialization"
                else:
                    admission_status = "admitted_replay"
                    audit_refs["materializationReadback"] = readback
                audit_refs.setdefault("phaseTimingsMs", {})[
                    "replayReadback"
                ] = round((time.monotonic() - started) * 1000, 3)
    else:
        replay_errors.append(
            "bridge admission replay validation: legacy replay row has no digest "
            "and is non-admitted"
        )
        admission_status = "legacy_non_admitted_replay"
    return BridgeDispatchResult(
        packetId=packet.packet_id,
        dispatchedAt=dispatched_at,
        taskRecords=[
            TaskDispatchRecord(
                taskId=task.id,
                owner=task.owner,
                reviewer=task.reviewer,
                status="already_dispatched",
            )
            for task in packet.tasks
        ],
        replayRejected=True,
        dryRun=dry_run,
        auditRefs=audit_refs,
        admissionRecord=admission_record,
        admissionStatus=admission_status,
        retryable=replay_retryable,
        errors=replay_errors,
    )


def _dispatch_task_packet_under_fence(
    request: BridgeDispatchRequest,
    *,
    key_store: Optional[Dict[str, bytes]] = None,
    runtime_env: Optional[Mapping[str, str]] = None,
) -> BridgeDispatchResult:
    """Verify, claim, and materialise one exact signed packet.

    Replay and claim locks cover only local compare-and-swap state.  Governed
    status subprocesses and canonical readback always execute after those
    locks are released.  A transient writer timeout therefore leaves an exact
    packet claim retryable instead of manufacturing a terminal failed receipt.
    """

    operation_started = time.monotonic()
    packet = request.packet
    repo_root = request.repo_root or _find_repo_root()
    dry_run = request.dry_run
    dispatched_at = _now()
    environment = _merged_environment(runtime_env)

    verification_started = time.monotonic()
    verify_packet(packet, key_store=key_store)
    violations = _check_constraints(packet)
    if violations:
        raise ValueError("Packet constraint violation: " + "; ".join(violations))

    digest = packet_digest(packet)
    audit_refs = _audit_refs(packet, dispatched_at)
    phase_timings: Dict[str, float] = {
        "verification": round((time.monotonic() - verification_started) * 1000, 3)
    }
    audit_refs["phaseTimingsMs"] = phase_timings

    claim_token: str | None = None
    replay: Mapping[str, object] | None = None
    claim_started = time.monotonic()
    with packet_replay_lock(repo_root=repo_root):
        if dry_run:
            replay = replay_record(
                packet.packet_id,
                repo_root=repo_root,
                lock_held=True,
            )
            claim_state = "dry_run"
            claim = {}
        else:
            claim_state, claim = _claim_packet_dispatch_locked(
                packet,
                repo_root=repo_root,
                digest=digest,
                environment=environment,
            )
            if claim_state == "replay":
                replay = claim
            elif claim_state == "claimed":
                claim_token = str(claim["claim_token"])
    phase_timings["replayClaim"] = round(
        (time.monotonic() - claim_started) * 1000,
        3,
    )
    audit_refs["dispatchClaim"] = {
        "state": claim_state,
        "ownerPid": claim.get("owner_pid"),
        "claimedAt": claim.get("claimed_at"),
        "expiresAt": claim.get("expires_at"),
        "taskSpecHashes": [
            item["task_spec_hash"] for item in _claim_identity(packet, digest)["tasks"]
        ],
    }

    if replay is not None:
        result = _replay_dispatch_result(
            packet,
            repo_root=repo_root,
            digest=digest,
            replay=replay,
            dispatched_at=dispatched_at,
            dry_run=dry_run,
            environment=environment,
            audit_refs=audit_refs,
        )
        phase_timings["total"] = round(
            (time.monotonic() - operation_started) * 1000,
            3,
        )
        return result

    if claim_state == "busy":
        phase_timings["total"] = round(
            (time.monotonic() - operation_started) * 1000,
            3,
        )
        return BridgeDispatchResult(
            packetId=packet.packet_id,
            dispatchedAt=dispatched_at,
            taskRecords=[
                TaskDispatchRecord(
                    taskId=task.id,
                    owner=task.owner,
                    reviewer=task.reviewer,
                    status="already_dispatched",
                )
                for task in packet.tasks
            ],
            # A concurrent exact duplicate is a replay attempt, but remains
            # retryable until the owning claim reaches durable admission.
            replayRejected=True,
            dryRun=False,
            auditRefs=audit_refs,
            admissionRecord=None,
            admissionStatus="dispatch_claim_retryable",
            retryable=True,
            errors=[
                f"packet dispatch is already claimed by pid {claim.get('owner_pid')} "
                f"until {claim.get('expires_at')}"
            ],
        )

    task_records: List[TaskDispatchRecord] = []
    errors: List[str] = []
    retryable = False
    dispatch_timings: Dict[str, float] = {}
    for task in packet.tasks:
        task_started = time.monotonic()
        record = _dispatch_task(
            task,
            packet=packet,
            repo_root=repo_root,
            dry_run=dry_run,
            environment=environment,
        )
        dispatch_timings[task.id] = round(
            (time.monotonic() - task_started) * 1000,
            3,
        )
        task_records.append(record)
        if record.error and record.status in {"error", "retryable"}:
            errors.append(f"{task.id}: {record.error}")
        retryable = retryable or record.status == "retryable"
    audit_refs["taskDispatchTimingsMs"] = dispatch_timings
    phase_timings["taskDispatch"] = round(sum(dispatch_timings.values()), 3)

    admission_record = None
    admission_status = "dry_run" if dry_run else "not_attempted"
    if errors:
        if retryable:
            admission_status = "task_state_mutation_retryable"
        _release_dispatch_claim(
            repo_root=repo_root,
            packet_id=packet.packet_id,
            claim_token=claim_token,
        )
    elif not dry_run:
        readback_started = time.monotonic()
        try:
            readback = _validate_materialized_tasks(
                packet,
                repo_root=repo_root,
                environment=environment,
            )
        except OSError as exc:
            errors.append(f"bridge materialization: {exc}")
            admission_status = "materialization_read_retryable"
            retryable = True
        except ValueError as exc:
            errors.append(f"bridge materialization: {exc}")
            admission_status = "invalid_materialization"
        else:
            audit_refs["materializationReadback"] = readback
        phase_timings["materializationReadback"] = round(
            (time.monotonic() - readback_started) * 1000,
            3,
        )
        if errors:
            _release_dispatch_claim(
                repo_root=repo_root,
                packet_id=packet.packet_id,
                claim_token=claim_token,
            )

    became_replay: Mapping[str, object] | None = None
    if not dry_run and not errors:
        commit_started = time.monotonic()
        with packet_replay_lock(repo_root=repo_root):
            current_replay = replay_record(
                packet.packet_id,
                repo_root=repo_root,
                lock_held=True,
            )
            if current_replay is not None:
                became_replay = current_replay
                _remove_dispatch_claim_locked(
                    repo_root=repo_root,
                    packet_id=packet.packet_id,
                    claim_token=claim_token or "",
                )
            else:
                claim_path = _dispatch_claim_path(repo_root, packet.packet_id)
                current_claim = _load_dispatch_claim(claim_path)
                if (
                    current_claim is None
                    or str(current_claim.get("claim_token") or "") != claim_token
                    or {
                        key: current_claim.get(key)
                        for key in ("packet_id", "packet_digest", "tasks")
                    }
                    != _claim_identity(packet, digest)
                ):
                    errors.append("bridge dispatch claim changed before admission commit")
                    admission_status = "dispatch_claim_lost_retryable"
                    retryable = True
                else:
                    try:
                        provenance = _admission_provenance(packet)
                        admission_record = persist_admission_record(
                            repo_root=repo_root,
                            packet_id=packet.packet_id,
                            packet_digest=digest,
                            admitted_at=dispatched_at,
                            packet_version=str(provenance["packet_version"]),
                            actor=provenance["actor"],
                            mode=str(provenance["mode"]),
                            intent=str(provenance["intent"]),
                            conversation_id=str(provenance["conversation_id"]),
                            source_turn_ids=provenance["source_turn_ids"],
                            documents=provenance["documents"],
                            audit_conversation_href=provenance[
                                "audit_conversation_href"
                            ],
                            emitted_at=str(provenance["emitted_at"]),
                            constraints=provenance["constraints"],
                            tasks=provenance["tasks"],
                            dispatch_records=[
                                record.model_dump(mode="json", by_alias=True)
                                for record in task_records
                            ],
                        )
                        admission_status = "admitted_unmarked"
                        mark_packet_seen(
                            packet.packet_id,
                            repo_root=repo_root,
                            digest=digest,
                            lock_held=True,
                        )
                        admission_status = "admitted"
                    except OSError as exc:
                        operation = (
                            "bridge replay mark"
                            if admission_record is not None
                            else "bridge admission"
                        )
                        errors.append(f"{operation}: {exc}")
                        admission_status = (
                            "replay_mark_persistence_retryable"
                            if admission_record is not None
                            else "admission_persistence_retryable"
                        )
                        retryable = True
                    except ValueError as exc:
                        operation = (
                            "bridge replay mark"
                            if admission_record is not None
                            else "bridge admission"
                        )
                        errors.append(f"{operation}: {exc}")
                        admission_status = (
                            "invalid_replay_mark"
                            if admission_record is not None
                            else "invalid_admission"
                        )
                    finally:
                        _remove_dispatch_claim_locked(
                            repo_root=repo_root,
                            packet_id=packet.packet_id,
                            claim_token=claim_token or "",
                        )
        phase_timings["admissionCommit"] = round(
            (time.monotonic() - commit_started) * 1000,
            3,
        )

    if became_replay is not None:
        result = _replay_dispatch_result(
            packet,
            repo_root=repo_root,
            digest=digest,
            replay=became_replay,
            dispatched_at=dispatched_at,
            dry_run=False,
            environment=environment,
            audit_refs=audit_refs,
        )
        phase_timings["total"] = round(
            (time.monotonic() - operation_started) * 1000,
            3,
        )
        return result

    phase_timings["total"] = round(
        (time.monotonic() - operation_started) * 1000,
        3,
    )
    return BridgeDispatchResult(
        packetId=packet.packet_id,
        dispatchedAt=dispatched_at,
        taskRecords=task_records,
        replayRejected=False,
        dryRun=dry_run,
        auditRefs=audit_refs,
        admissionRecord=admission_record,
        admissionStatus=admission_status,
        retryable=retryable,
        errors=errors,
    )


def dispatch_task_packet(
    request: BridgeDispatchRequest,
    *,
    key_store: Optional[Dict[str, bytes]] = None,
    runtime_env: Optional[Mapping[str, str]] = None,
) -> BridgeDispatchResult:
    """Fence one verified packet before entering the off-lock dispatcher.

    A JSON claim may expire for crash recovery, but a replacement must never
    overlap an original claimant that is still executing.  `flock` releases on
    process death and remains held across assignment, readback, and admission;
    the governed child never opens this fence.
    """

    packet = request.packet
    repo_root = request.repo_root or _find_repo_root()
    if request.dry_run:
        return _dispatch_task_packet_under_fence(
            request,
            key_store=key_store,
            runtime_env=runtime_env,
        )

    verify_packet(packet, key_store=key_store)
    violations = _check_constraints(packet)
    if violations:
        raise ValueError("Packet constraint violation: " + "; ".join(violations))

    descriptor = _try_acquire_dispatch_fence(repo_root, packet.packet_id)
    if descriptor is None:
        dispatched_at = _now()
        audit_refs = _audit_refs(packet, dispatched_at)
        audit_refs["dispatchFence"] = {
            "state": "busy",
            "path": str(_dispatch_fence_path(repo_root, packet.packet_id)),
        }
        return BridgeDispatchResult(
            packetId=packet.packet_id,
            dispatchedAt=dispatched_at,
            taskRecords=[
                TaskDispatchRecord(
                    taskId=task.id,
                    owner=task.owner,
                    reviewer=task.reviewer,
                    status="already_dispatched",
                )
                for task in packet.tasks
            ],
            replayRejected=True,
            dryRun=False,
            auditRefs=audit_refs,
            admissionRecord=None,
            admissionStatus="dispatch_fence_retryable",
            retryable=True,
            errors=["packet dispatch is fenced by a live claimant"],
        )
    try:
        return _dispatch_task_packet_under_fence(
            request,
            key_store=key_store,
            runtime_env=runtime_env,
        )
    finally:
        _release_dispatch_fence(descriptor)
