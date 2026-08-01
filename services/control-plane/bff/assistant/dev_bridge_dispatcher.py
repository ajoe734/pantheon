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

import hashlib
import json
import os
import subprocess
import sys
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


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


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
    try:
        result = subprocess.run(
            command,
            env=dict(environment),
            capture_output=True,
            text=True,
            timeout=30,
            cwd=repo_root,
        )
    except subprocess.TimeoutExpired as exc:
        raise OSError(f"{label} timed out after 30s") from exc
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


def _canonical_task_state_readback(
    packet: DevTaskPacket,
    *,
    repo_root: str,
    environment: Mapping[str, str],
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

    task_readbacks: List[Dict[str, object]] = []
    for task in packet.tasks:
        payload = _run_readback_command(
            [sys.executable, ai_status, "show", task.id],
            environment=command_environment,
            repo_root=repo_root,
            label=f"canonical task-state readback for {task.id}",
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
        "taskIds": [task.id for task in packet.tasks],
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

    cmd = [
        sys.executable,
        ai_status,
        "assign",
        task.id,
        task.owner,
        task.reviewer,
        task.title,
    ]

    try:
        result = subprocess.run(
            cmd,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=repo_root,
        )
        if result.returncode != 0:
            record.status = "error"
            record.error = (result.stderr or result.stdout or "non-zero exit").strip()[:500]
    except subprocess.TimeoutExpired:
        record.status = "error"
        record.error = "ai_status.py assign timed out after 30s"
    except OSError as exc:
        record.status = "error"
        record.error = str(exc)

    return record


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def dispatch_task_packet(
    request: BridgeDispatchRequest,
    *,
    key_store: Optional[Dict[str, bytes]] = None,
    runtime_env: Optional[Mapping[str, str]] = None,
) -> BridgeDispatchResult:
    """Verify, replay-check, and materialise all tasks in a signed DevTaskPacket.

    Returns BridgeDispatchResult.  Never raises for per-task failures — errors
    are captured in result.errors and per-task TaskDispatchRecord.error.

    Raises ValueError when:
    - Packet signature is invalid.
    - Packet constraints are violated.
    (Replay rejection is not raised; it returns a result with replay_rejected=True.)
    """
    packet = request.packet
    repo_root = request.repo_root or _find_repo_root()
    dry_run = request.dry_run
    dispatched_at = _now()
    environment = _merged_environment(runtime_env)

    # 1. Signature verification (raises on failure)
    verify_packet(packet, key_store=key_store)

    # 2. Constraint check (raises on violation)
    violations = _check_constraints(packet)
    if violations:
        raise ValueError("Packet constraint violation: " + "; ".join(violations))

    digest = packet_digest(packet)
    audit_refs = _audit_refs(packet, dispatched_at)

    # Replay check and successful terminal mark share one cross-process lock.
    # A failed/partial packet remains retryable; already successful tasks are
    # made no-ops by the bridge assignment metadata in scripts/ai_status.py.
    with packet_replay_lock(repo_root=repo_root):
        replay = replay_record(packet.packet_id, repo_root=repo_root, lock_held=True)
        if replay is not None:
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

        task_records: List[TaskDispatchRecord] = []
        errors: List[str] = []
        for task in packet.tasks:
            rec = _dispatch_task(
                task,
                packet=packet,
                repo_root=repo_root,
                dry_run=dry_run,
                environment=environment,
            )
            task_records.append(rec)
            if rec.status == "error" and rec.error:
                errors.append(f"{task.id}: {rec.error}")

        admission_record = None
        admission_status = "dry_run" if dry_run else "not_attempted"
        retryable = False
        if not dry_run and not errors:
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
        if not dry_run and not errors:
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
                    audit_conversation_href=provenance["audit_conversation_href"],
                    emitted_at=str(provenance["emitted_at"]),
                    constraints=provenance["constraints"],
                    tasks=provenance["tasks"],
                    dispatch_records=[
                        record.model_dump(mode="json", by_alias=True)
                        for record in task_records
                    ],
                )
                admission_status = "admitted_unmarked"
            except OSError as exc:
                errors.append(f"bridge admission: {exc}")
                admission_status = "admission_persistence_retryable"
                retryable = True
            except ValueError as exc:
                errors.append(f"bridge admission: {exc}")
                admission_status = "invalid_admission"

        if not dry_run and not errors:
            try:
                mark_packet_seen(
                    packet.packet_id,
                    repo_root=repo_root,
                    digest=digest,
                    lock_held=True,
                )
                admission_status = "admitted"
            except OSError as exc:
                errors.append(f"bridge replay mark: {exc}")
                admission_status = "replay_mark_persistence_retryable"
                retryable = True
            except ValueError as exc:
                errors.append(f"bridge replay mark: {exc}")
                admission_status = "invalid_replay_mark"

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
