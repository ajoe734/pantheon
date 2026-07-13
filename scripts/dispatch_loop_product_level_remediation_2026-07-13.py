#!/usr/bin/env python3
"""Validate and dispatch the 2026-07-13 loop product-level remediation DAG."""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any, Iterator


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG_PATH = (
    REPO_ROOT
    / "docs"
    / "bff"
    / "execution-tasks"
    / "2026-07-13-loop-product-level-remediation"
    / "tasks.json"
)
STATUS_ROOT = Path(
    os.path.expanduser(os.environ.get("PANTHEON_STATUS_ROOT", str(REPO_ROOT)))
).resolve()
STATUS_PATH = STATUS_ROOT / "ai-status.json"
LOG_PATH = STATUS_ROOT / "ai-activity-log.jsonl"
ARCHIVE_ROOT = STATUS_ROOT / "ai-task-archive" / "tasks"

AUTO_BY = "dispatch_loop_product_level_remediation_2026-07-13"
TERMINAL_STATUSES = {"done", "superseded", "cancelled"}
DEPENDENCY_DONE_STATUSES = {"done"}
REQUIRED_TASK_FIELDS = {
    "id",
    "title",
    "summary_zh",
    "phase",
    "owner",
    "reviewer",
    "status",
    "depends_on",
    "artifacts",
    "acceptance",
    "next",
    "wave",
    "fleet_lane",
    "target_repo",
    "merge_target",
    "loop_ids",
    "current_maturity",
    "target_maturity",
    "desired_state_sources",
    "actual_state_sources",
    "proof_required",
    "non_goals",
    "dispatch_rules",
    "product_level_required",
    "evidence_root",
    "task_doc",
    "requires_human_ops_signoff",
}
REQUIRED_NON_GOALS = {
    "No panel-only closure",
    "No seed fixture as live proof",
    "No approval gate bypass",
}
SUPPORTED_REPOS = {"pantheon", "execute-plans"}
LIVE_ADMISSION_FIELDS = {
    "active_worker",
    "attempt",
    "branch",
    "claim_token",
    "claimed_by",
    "current_attempt_id",
    "dispatch_token",
    "payload_signature",
    "review_started_at",
    "run_id",
    "started_at",
    "task_signature",
    "worker",
    "worker_pid",
    "worker_run_id",
}


class DispatchError(RuntimeError):
    """Fail-closed packet or live-state validation error."""


def iso_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def catalog_path() -> Path:
    override = str(os.environ.get("LOOP_PRODUCT_TASK_CATALOG") or "").strip()
    if not override:
        return DEFAULT_CATALOG_PATH
    candidate = Path(os.path.expanduser(override))
    if not candidate.is_absolute():
        candidate = REPO_ROOT / candidate
    return candidate.resolve()


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DispatchError(f"JSON file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise DispatchError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise DispatchError(f"expected JSON object in {path}")
    return payload


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical_json_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return sha256_bytes(encoded)


def artifact_overlaps(left: str, right: str) -> bool:
    left_clean = left.strip().replace("\\", "/").rstrip("/")
    right_clean = right.strip().replace("\\", "/").rstrip("/")
    if not left_clean or not right_clean:
        return False
    return (
        left_clean == right_clean
        or left_clean.startswith(right_clean + "/")
        or right_clean.startswith(left_clean + "/")
    )


def internal_ancestors(
    task_id: str,
    by_id: dict[str, dict[str, Any]],
    memo: dict[str, set[str]],
) -> set[str]:
    if task_id in memo:
        return memo[task_id]
    ancestors: set[str] = set()
    for dep_id in by_id[task_id]["depends_on"]:
        if dep_id not in by_id:
            continue
        ancestors.add(dep_id)
        ancestors.update(internal_ancestors(dep_id, by_id, memo))
    memo[task_id] = ancestors
    return ancestors


def validate_catalog(payload: dict[str, Any], path: Path) -> list[dict[str, Any]]:
    schema_version = payload.get("schema_version")
    if not isinstance(schema_version, int) or schema_version < 1:
        raise DispatchError("catalog schema_version must be a positive integer")
    tasks = payload.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise DispatchError("catalog tasks must be a non-empty list")
    if payload.get("task_count") != len(tasks):
        raise DispatchError(
            f"catalog task_count={payload.get('task_count')!r} does not match {len(tasks)}"
        )

    allowed_owners = set(payload.get("allowed_owners") or [])
    if allowed_owners != {"Codex", "Codex2"}:
        raise DispatchError(
            "catalog allowed_owners must be exactly Codex and Codex2 for current fleet readiness"
        )
    external = payload.get("external_dependencies")
    if not isinstance(external, list) or len(external) != len(set(external)):
        raise DispatchError("external_dependencies must be a unique list")
    external_ids = {str(item) for item in external}
    additive_task_ids = payload.get("additive_task_ids") or []
    if schema_version >= 2:
        if (
            not isinstance(additive_task_ids, list)
            or not additive_task_ids
            or len(additive_task_ids) != len(set(map(str, additive_task_ids)))
        ):
            raise DispatchError("schema v2 additive_task_ids must be a unique non-empty list")
        if payload.get("task_doc_contract_source") != "tasks.json":
            raise DispatchError("schema v2 task_doc_contract_source must be tasks.json")
    additive_ids = {str(item) for item in additive_task_ids}

    by_id: dict[str, dict[str, Any]] = {}
    for index, raw_task in enumerate(tasks):
        if not isinstance(raw_task, dict):
            raise DispatchError(f"tasks[{index}] must be an object")
        task = deepcopy(raw_task)
        missing = sorted(REQUIRED_TASK_FIELDS - set(task))
        if missing:
            raise DispatchError(
                f"{task.get('id', f'tasks[{index}]')} missing fields: {', '.join(missing)}"
            )
        task_id = str(task["id"]).strip()
        if not task_id.startswith("LOOP-PROD-"):
            raise DispatchError(f"invalid task id outside LOOP-PROD namespace: {task_id}")
        if task_id in by_id:
            raise DispatchError(f"duplicate task id: {task_id}")
        if task["status"] != "todo":
            raise DispatchError(f"{task_id} catalog status must be todo")
        if task["owner"] not in allowed_owners or task["reviewer"] not in allowed_owners:
            raise DispatchError(f"{task_id} owner/reviewer is not an enabled fleet")
        if task["owner"] == task["reviewer"]:
            raise DispatchError(f"{task_id} owner and reviewer must differ")
        if task["target_repo"] not in SUPPORTED_REPOS:
            raise DispatchError(f"{task_id} unsupported target_repo={task['target_repo']!r}")
        if task["merge_target"] != "dev":
            raise DispatchError(f"{task_id} merge_target must be dev")
        if not isinstance(task["wave"], int) or task["wave"] < 0:
            raise DispatchError(f"{task_id} wave must be a non-negative integer")
        for field in (
            "depends_on",
            "artifacts",
            "acceptance",
            "loop_ids",
            "desired_state_sources",
            "actual_state_sources",
            "proof_required",
            "non_goals",
            "dispatch_rules",
        ):
            value = task[field]
            if not isinstance(value, list) or (field != "depends_on" and not value):
                raise DispatchError(f"{task_id} {field} must be a non-empty list")
            if len(value) != len(set(str(item) for item in value)):
                raise DispatchError(f"{task_id} {field} contains duplicates")
        if task_id in task["depends_on"]:
            raise DispatchError(f"{task_id} depends on itself")
        if not REQUIRED_NON_GOALS.issubset(set(task["non_goals"])):
            raise DispatchError(f"{task_id} is missing canonical non_goals")
        if task["product_level_required"] is not True:
            raise DispatchError(f"{task_id} must require product-level outcome")
        if not isinstance(task["requires_human_ops_signoff"], bool):
            raise DispatchError(
                f"{task_id} requires_human_ops_signoff must be a boolean"
            )
        if not str(task["current_maturity"]).strip() or not str(
            task["target_maturity"]
        ).strip():
            raise DispatchError(f"{task_id} maturity fields cannot be blank")
        if task["target_maturity"] not in {
            "contract",
            "integrated",
            "reconciled",
            "proven-live",
            "product-level",
        }:
            raise DispatchError(
                f"{task_id} has unsupported target_maturity={task['target_maturity']!r}"
            )

        artifacts = [str(item).strip() for item in task["artifacts"]]
        if task["target_repo"] == "execute-plans":
            if any(not item.startswith("execute-plans/") for item in artifacts):
                raise DispatchError(
                    f"{task_id} execute-plans artifacts must all use execute-plans/ slash routing"
                )
            if not str(task["evidence_root"]).startswith("execute-plans/"):
                raise DispatchError(
                    f"{task_id} execute-plans evidence_root must stay in execute-plans"
                )
        else:
            if any(
                item.startswith(("execute-plans/", "execute-plans:", "front-ai-trading-system/"))
                for item in artifacts
            ):
                raise DispatchError(
                    f"{task_id} Pantheon task contains a cross-repo or legacy artifact"
                )
            if str(task["evidence_root"]).startswith("execute-plans/"):
                raise DispatchError(
                    f"{task_id} Pantheon evidence_root cannot route to execute-plans"
                )

        task_doc = Path(str(task["task_doc"]))
        if task_doc.is_absolute() or ".." in task_doc.parts:
            raise DispatchError(f"{task_id} task_doc must be repo-relative")
        if not (REPO_ROOT / task_doc).is_file():
            raise DispatchError(f"{task_id} task_doc does not exist: {task_doc}")
        if task_id in additive_ids:
            document = (REPO_ROOT / task_doc).read_text(encoding="utf-8")
            contract_digest = canonical_json_sha256(
                {
                    "acceptance": task["acceptance"],
                    "proof_required": task["proof_required"],
                    "dispatch_rules": task["dispatch_rules"],
                }
            )
            contract_marker = f"Canonical contract SHA-256: `{contract_digest}`"
            if contract_marker not in document:
                raise DispatchError(
                    f"{task_id} task_doc canonical contract marker is stale or missing"
                )
        by_id[task_id] = task

    if schema_version >= 2:
        unknown_additive = sorted(additive_ids - set(by_id))
        if unknown_additive:
            raise DispatchError(
                "additive_task_ids contains unknown tasks: " + ", ".join(unknown_additive)
            )

    for task_id, task in by_id.items():
        for dep_id in task["depends_on"]:
            if dep_id not in by_id and dep_id not in external_ids:
                raise DispatchError(f"{task_id} depends on undeclared task {dep_id}")
            if dep_id in by_id and by_id[dep_id]["wave"] > task["wave"]:
                raise DispatchError(
                    f"{task_id} wave {task['wave']} depends on later wave "
                    f"{dep_id}={by_id[dep_id]['wave']}"
                )

    if schema_version >= 2:
        migrations = payload.get("catalog_migrations")
        if not isinstance(migrations, list) or not migrations:
            raise DispatchError("schema v2 catalog_migrations must be non-empty")
        migration_ids: set[str] = set()
        patched_task_ids: set[str] = set()
        for migration in migrations:
            if not isinstance(migration, dict):
                raise DispatchError("catalog migration must be an object")
            migration_id = str(migration.get("id") or "").strip()
            if not migration_id or migration_id in migration_ids:
                raise DispatchError("catalog migration IDs must be unique and non-empty")
            migration_ids.add(migration_id)
            from_digest = str(migration.get("from_catalog_sha256") or "")
            if len(from_digest) != 64 or any(
                character not in "0123456789abcdef" for character in from_digest
            ):
                raise DispatchError(f"{migration_id} has invalid from_catalog_sha256")
            patches = migration.get("required_live_task_patches")
            if not isinstance(patches, list) or not patches:
                raise DispatchError(f"{migration_id} must declare live task patches")
            for patch in patches:
                if not isinstance(patch, dict):
                    raise DispatchError(f"{migration_id} task patch must be an object")
                task_id = str(patch.get("task_id") or "").strip()
                if task_id not in by_id or task_id in patched_task_ids:
                    raise DispatchError(
                        f"{migration_id} task patch target is missing or duplicated: {task_id}"
                    )
                patched_task_ids.add(task_id)
                before = patch.get("before_depends_on")
                appended = patch.get("append_dependencies")
                if (
                    not isinstance(before, list)
                    or not isinstance(appended, list)
                    or not appended
                    or len(before) != len(set(map(str, before)))
                    or len(appended) != len(set(map(str, appended)))
                ):
                    raise DispatchError(f"{migration_id} {task_id} has invalid dependency lists")
                expected = [*map(str, before), *map(str, appended)]
                if by_id[task_id]["depends_on"] != expected:
                    raise DispatchError(
                        f"{migration_id} {task_id} catalog dependencies do not match migration"
                    )
                if not set(map(str, appended)).issubset(additive_ids):
                    raise DispatchError(
                        f"{migration_id} {task_id} may append only additive task IDs"
                    )

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(task_id: str) -> None:
        if task_id in visiting:
            raise DispatchError(f"dependency cycle detected at {task_id}")
        if task_id in visited:
            return
        visiting.add(task_id)
        for dep_id in by_id[task_id]["depends_on"]:
            if dep_id in by_id:
                visit(dep_id)
        visiting.remove(task_id)
        visited.add(task_id)

    for task_id in by_id:
        visit(task_id)

    ancestor_memo: dict[str, set[str]] = {}
    for task_id in by_id:
        internal_ancestors(task_id, by_id, ancestor_memo)
    task_items = list(by_id.items())

    if schema_version >= 2:
        authority = payload.get("completion_authority")
        if not isinstance(authority, dict):
            raise DispatchError("schema v2 completion_authority must be an object")
        authority_id = str(authority.get("task_id") or "")
        checkpoint_ids = {
            str(item) for item in authority.get("checkpoint_only_task_ids") or []
        }
        guard_id = str(authority.get("guard_install_task_id") or "")
        if authority_id not in by_id or guard_id not in by_id:
            raise DispatchError("completion authority or guard task is missing")
        if authority.get("requires_protected_human_ops_verdict") is not True:
            raise DispatchError("completion authority must require protected Human/Ops verdict")
        if by_id[authority_id]["requires_human_ops_signoff"] is not True:
            raise DispatchError("completion authority task must require Human/Ops signoff")
        if not checkpoint_ids or authority_id in checkpoint_ids:
            raise DispatchError("completion authority checkpoint-only set is invalid")
        if not checkpoint_ids.issubset(by_id):
            raise DispatchError("completion authority references an unknown checkpoint")
        if guard_id not in ancestor_memo[authority_id]:
            raise DispatchError("completion guard must be an ancestor of final authority")
        dependents = {
            dep_id
            for task in by_id.values()
            for dep_id in task["depends_on"]
            if dep_id in by_id
        }
        sinks = set(by_id) - dependents
        if sinks != {authority_id}:
            raise DispatchError(
                "completion authority must be the unique sink; got "
                + ", ".join(sorted(sinks))
            )
        if ancestor_memo[authority_id] != set(by_id) - {authority_id}:
            raise DispatchError("every other primary task must be an ancestor of final authority")
        bindings = authority.get("verdict_binding_fields")
        required_bindings = {
            "program_id",
            "catalog_sha256",
            "closeout_manifest_sha256",
            "target_environment",
            "frontend_sha",
            "bff_sha",
            "actor_id",
            "actor_role",
            "decision",
            "issued_at",
            "expires_at",
            "nonce",
        }
        if not isinstance(bindings, list) or not required_bindings.issubset(
            set(map(str, bindings))
        ):
            raise DispatchError("completion authority verdict bindings are incomplete")

    for left_index, (left_id, left) in enumerate(task_items):
        for right_id, right in task_items[left_index + 1 :]:
            if left["target_repo"] != right["target_repo"]:
                continue
            overlaps = any(
                artifact_overlaps(str(left_artifact), str(right_artifact))
                for left_artifact in left["artifacts"]
                for right_artifact in right["artifacts"]
            )
            if not overlaps:
                continue
            ordered = (
                left_id in ancestor_memo[right_id]
                or right_id in ancestor_memo[left_id]
            )
            if not ordered:
                raise DispatchError(
                    "overlapping artifact scopes require an explicit dependency order: "
                    f"{left_id} <-> {right_id}"
                )

    return [by_id[str(item["id"])] for item in tasks]


def archive_status(path: Path) -> str:
    payload = read_json(path)
    task_payload = payload.get("task") if isinstance(payload.get("task"), dict) else {}
    return str(
        payload.get("terminal_status")
        or task_payload.get("status")
        or payload.get("status")
        or ""
    ).strip()


def dependency_state(
    dep_id: str,
    active_by_id: dict[str, dict[str, Any]],
) -> tuple[str, str]:
    active = active_by_id.get(dep_id)
    if active is not None:
        return str(active.get("status") or "unknown"), "active"
    archived = ARCHIVE_ROOT / f"{dep_id}.json"
    if archived.is_file():
        return archive_status(archived), "archive"
    return "missing", "missing"


def validate_live_state(
    state: dict[str, Any],
    catalog: dict[str, Any],
    tasks: list[dict[str, Any]],
) -> None:
    wave_state = state.get("wave_state")
    if isinstance(wave_state, dict) and wave_state.get("status") == "frozen":
        raise DispatchError(
            "current planning wave is frozen; direct task assignment is fail-closed"
        )
    active_tasks = state.get("tasks")
    if not isinstance(active_tasks, list):
        raise DispatchError("ai-status.json tasks must be a list")
    active_by_id = {
        str(task.get("id")): task
        for task in active_tasks
        if isinstance(task, dict) and str(task.get("id") or "").strip()
    }
    agents = {
        str(agent.get("name"))
        for agent in state.get("agents", [])
        if isinstance(agent, dict)
    }
    required_agents = {task["owner"] for task in tasks} | {
        task["reviewer"] for task in tasks
    }
    missing_agents = sorted(required_agents - agents)
    if missing_agents:
        raise DispatchError(
            "enabled fleet agents are missing from status: " + ", ".join(missing_agents)
        )

    for dep_id in catalog["external_dependencies"]:
        status, source = dependency_state(str(dep_id), active_by_id)
        if source == "missing":
            raise DispatchError(f"external dependency is missing: {dep_id}")
        if source == "archive" and status not in DEPENDENCY_DONE_STATUSES:
            raise DispatchError(
                f"archived external dependency {dep_id} is {status!r}; only done can satisfy"
            )
        if source == "active" and status in {"superseded", "cancelled"}:
            raise DispatchError(
                f"active external dependency {dep_id} is terminal {status!r}; only done can satisfy"
            )


def file_signature(path: Path) -> tuple[int, int, int, int, str]:
    data = path.read_bytes()
    stat_result = path.stat()
    return (
        stat_result.st_dev,
        stat_result.st_ino,
        stat_result.st_mtime_ns,
        stat_result.st_size,
        sha256_bytes(data),
    )


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        if path.exists():
            os.chmod(temp_path, path.stat().st_mode)
        os.replace(temp_path, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def append_logs(entries: list[dict[str, Any]]) -> None:
    if not entries:
        return
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def build_task(
    task: dict[str, Any],
    catalog: dict[str, Any],
    catalog_digest: str,
    timestamp: str,
) -> dict[str, Any]:
    result = deepcopy(task)
    authority = catalog.get("completion_authority") or {}
    authority_id = str(authority.get("task_id") or "")
    checkpoint_ids = {
        str(item) for item in authority.get("checkpoint_only_task_ids") or []
    }
    completion_role = "ordinary"
    if task["id"] == authority_id:
        completion_role = "final_authority"
    elif task["id"] in checkpoint_ids:
        completion_role = "checkpoint_only"
    selected_catalog_path = catalog_path()
    try:
        catalog_ref = str(selected_catalog_path.relative_to(REPO_ROOT))
    except ValueError:
        catalog_ref = str(selected_catalog_path)
    result.update(
        {
            "created_at": timestamp,
            "last_update": timestamp,
            "task_class": "execution",
            "auto_created_by": AUTO_BY,
            "auto_generated": True,
            "delivery_layer": "primary",
            "mutates_canonical": True,
            "helper_kind": "loop_product_level_execution_slice",
            "completion_role": completion_role,
            "source_ref": {
                "plan": catalog["source_plan"],
                "packet": catalog["packet"],
                "catalog": catalog_ref,
                "catalog_sha256": catalog_digest,
                "program_id": catalog["program_id"],
            },
        }
    )
    return result


def archived_primary_status(task_id: str) -> str | None:
    path = ARCHIVE_ROOT / f"{task_id}.json"
    if not path.is_file():
        return None
    status = archive_status(path)
    if status not in TERMINAL_STATUSES:
        raise DispatchError(
            f"archive collision for {task_id} has non-terminal status {status!r}"
        )
    return status


def _has_live_admission(task: dict[str, Any]) -> bool:
    return any(task.get(field) not in (None, "", 0, False, [], {}) for field in LIVE_ADMISSION_FIELDS)


def apply_catalog_migrations(
    state: dict[str, Any],
    catalog: dict[str, Any],
    catalog_digest: str,
    timestamp: str,
) -> tuple[list[str], list[dict[str, Any]], bool]:
    migrations = catalog.get("catalog_migrations") or []
    if not migrations:
        return [], [], False

    active_tasks = state.get("tasks") or []
    active_by_id = {
        str(task.get("id")): task
        for task in active_tasks
        if isinstance(task, dict) and str(task.get("id") or "").strip()
    }
    has_program_tasks = any(task_id.startswith("LOOP-PROD-") for task_id in active_by_id)
    records = state.get("program_catalog_migrations")
    if records is None:
        records = []
    if not isinstance(records, list):
        raise DispatchError("program_catalog_migrations must be a list")
    record_by_id = {
        str(record.get("id")): record
        for record in records
        if isinstance(record, dict) and str(record.get("id") or "").strip()
    }

    migrated: list[str] = []
    logs: list[dict[str, Any]] = []
    pending_records: list[dict[str, Any]] = []

    for migration in migrations:
        migration_id = str(migration["id"])
        patches = migration["required_live_task_patches"]
        present = [str(patch["task_id"]) in active_by_id for patch in patches]
        if not any(present):
            if has_program_tasks:
                raise DispatchError(
                    f"{migration_id} cannot patch a partial live program: targets are missing"
                )
            continue
        if not all(present):
            raise DispatchError(
                f"{migration_id} requires every live patch target in one transaction"
            )

        modes: list[str] = []
        patch_records: list[dict[str, Any]] = []
        for patch in patches:
            task_id = str(patch["task_id"])
            existing = active_by_id[task_id]
            before = [str(item) for item in patch["before_depends_on"]]
            after = [*before, *map(str, patch["append_dependencies"])]
            current = existing.get("depends_on")
            if current == after:
                modes.append("after")
                continue
            if current != before:
                raise DispatchError(
                    f"{migration_id} {task_id} dependency preimage changed; no write performed"
                )
            source_ref = existing.get("source_ref")
            if not isinstance(source_ref, dict):
                raise DispatchError(f"{migration_id} {task_id} has no source_ref")
            if source_ref.get("program_id") != catalog["program_id"]:
                raise DispatchError(f"{migration_id} {task_id} belongs to another program")
            if source_ref.get("catalog_sha256") != migration["from_catalog_sha256"]:
                raise DispatchError(
                    f"{migration_id} {task_id} catalog preimage digest changed"
                )
            if existing.get("status") != "todo" or _has_live_admission(existing):
                raise DispatchError(
                    f"{migration_id} {task_id} is no longer pristine todo; no write performed"
                )
            modes.append("before")
            patch_records.append(
                {
                    "task_id": task_id,
                    "before_depends_on_sha256": canonical_json_sha256(before),
                    "after_depends_on_sha256": canonical_json_sha256(after),
                    "appended_dependencies": list(map(str, patch["append_dependencies"])),
                }
            )

        if len(set(modes)) != 1:
            raise DispatchError(
                f"{migration_id} is partially applied; refusing an unaudited repair"
            )
        if modes[0] == "after":
            record = record_by_id.get(migration_id)
            if record is None:
                source_digests = {
                    str(active_by_id[str(patch["task_id"])].get("source_ref", {}).get("catalog_sha256") or "")
                    for patch in patches
                }
                if source_digests != {catalog_digest}:
                    raise DispatchError(
                        f"{migration_id} dependencies changed without an audit record"
                    )
            elif (
                record.get("from_catalog_sha256") != migration["from_catalog_sha256"]
                or record.get("to_catalog_sha256") != catalog_digest
            ):
                raise DispatchError(f"{migration_id} audit record digest mismatch")
            continue

        for patch in patches:
            task_id = str(patch["task_id"])
            active_by_id[task_id]["depends_on"] = [
                *map(str, patch["before_depends_on"]),
                *map(str, patch["append_dependencies"]),
            ]
            migrated.append(task_id)
            logs.append(
                {
                    "ts": timestamp,
                    "agent": os.environ.get("AI_NAME", "Codex"),
                    "type": "dependency_migration",
                    "task_id": task_id,
                    "message": f"Applied {migration_id} exact dependency migration to {task_id}",
                }
            )
        pending_records.append(
            {
                "id": migration_id,
                "program_id": catalog["program_id"],
                "from_catalog_sha256": migration["from_catalog_sha256"],
                "to_catalog_sha256": catalog_digest,
                "applied_at": timestamp,
                "patches": patch_records,
            }
        )

    if not migrated:
        return [], [], False
    state["program_catalog_migrations"] = [*records, *pending_records]
    state["updated_at"] = timestamp
    return migrated, logs, True


def materialize(
    state: dict[str, Any],
    tasks: list[dict[str, Any]],
    catalog: dict[str, Any],
    catalog_digest: str,
    timestamp: str,
) -> tuple[list[str], list[str], list[str], list[dict[str, Any]], bool]:
    active_tasks = state.setdefault("tasks", [])
    active_by_id = {
        str(task.get("id")): task
        for task in active_tasks
        if isinstance(task, dict) and str(task.get("id") or "").strip()
    }
    created: list[str] = []
    preserved: list[str] = []
    archived: list[str] = []
    logs: list[dict[str, Any]] = []
    state_changed = False

    for task in tasks:
        task_id = task["id"]
        archive_state = archived_primary_status(task_id)
        if archive_state is not None:
            archived.append(f"{task_id}:{archive_state}")
            continue

        existing = active_by_id.get(task_id)
        if existing is not None:
            preserved.append(f"{task_id}:{existing.get('status', 'unknown')}")
            continue

        materialized = build_task(task, catalog, catalog_digest, timestamp)
        active_tasks.append(materialized)
        active_by_id[task_id] = materialized
        created.append(task_id)
        logs.append(
            {
                "ts": timestamp,
                "agent": os.environ.get("AI_NAME", "Codex"),
                "type": "assign",
                "task_id": task_id,
                "message": (
                    f"Assigned {task_id} to {materialized['owner']} "
                    f"with reviewer {materialized['reviewer']} from {catalog['program_id']}"
                ),
            }
        )
        state_changed = True

    if state_changed:
        state["updated_at"] = timestamp
    return created, preserved, archived, logs, state_changed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate the catalog, task documents, repository routing, and DAG only.",
    )
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate live dependencies and print the dispatch without writing state.",
    )
    return parser.parse_args()


def report(
    created: list[str],
    preserved: list[str],
    archived: list[str],
    migrated: list[str],
    *,
    dry_run: bool,
) -> None:
    for task_id in migrated:
        print(f"MIGRATE {task_id}")
    for task_id in created:
        print(f"CREATE {task_id}")
    for item in preserved:
        print(f"PRESERVE {item}")
    for item in archived:
        print(f"SKIP-ARCHIVED {item}")
    print(
        f"summary migrate={len(migrated)} create={len(created)} preserve={len(preserved)} "
        f"archived={len(archived)} total={len(created)+len(preserved)+len(archived)}"
    )
    if dry_run:
        print(f"Dry run only. No writes made. status_root={STATUS_ROOT}")


def main() -> int:
    args = parse_args()
    selected_catalog_path = catalog_path()
    catalog_bytes = selected_catalog_path.read_bytes()
    catalog = read_json(selected_catalog_path)
    tasks = validate_catalog(catalog, selected_catalog_path)
    print(
        f"Catalog valid: program={catalog['program_id']} tasks={len(tasks)} "
        f"sha256={sha256_bytes(catalog_bytes)}"
    )
    if args.validate_only:
        return 0
    if not STATUS_PATH.is_file():
        raise DispatchError(f"status file not found: {STATUS_PATH}")

    with STATUS_PATH.open("r+", encoding="utf-8") as status_handle:
        try:
            fcntl.flock(status_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise DispatchError(
                "another loop-product dispatcher holds the status lock"
            ) from exc

        original_signature = file_signature(STATUS_PATH)
        status_handle.seek(0)
        try:
            state = json.load(status_handle)
        except json.JSONDecodeError as exc:
            raise DispatchError(f"invalid JSON in {STATUS_PATH}: {exc}") from exc
        if not isinstance(state, dict):
            raise DispatchError("ai-status.json must contain an object")

        validate_live_state(state, catalog, tasks)
        timestamp = iso_now()
        catalog_digest = sha256_bytes(catalog_bytes)
        migrated, migration_logs, migration_changed = apply_catalog_migrations(
            state,
            catalog,
            catalog_digest,
            timestamp,
        )
        created, preserved, archived, logs, changed = materialize(
            state,
            tasks,
            catalog,
            catalog_digest,
            timestamp,
        )
        logs = [*migration_logs, *logs]
        changed = migration_changed or changed
        report(created, preserved, archived, migrated, dry_run=args.dry_run)

        if args.dry_run or not changed:
            if not changed:
                print("No state changes required.")
            return 0

        if file_signature(STATUS_PATH) != original_signature:
            raise DispatchError(
                "ai-status.json changed concurrently; no write performed, rerun dispatch"
            )
        atomic_write_json(STATUS_PATH, state)
        append_logs(logs)
        print(f"Updated {STATUS_PATH} atomically.")
        print(
            "Run PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon "
            "python3 scripts/ai_status.py sync to refresh generated views."
        )
        return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (DispatchError, FileNotFoundError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
