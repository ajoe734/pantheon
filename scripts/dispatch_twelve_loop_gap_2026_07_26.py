#!/usr/bin/env python3
"""Validate and materialize the 2026-07-26 twelve-loop remediation DAG.

The catalog is validated as one immutable graph before any live mutation.
Materialization then uses the canonical ``scripts/ai_status.py assign`` writer
one task at a time. Exact active or successfully archived catalog tasks are
skipped; malformed, non-successful, or conflicting IDs fail closed. This avoids
the DevTaskPacket bulk delimiter/partial-replay limitation while preserving the
repository task-state locks and audit log.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
from typing import Any, Callable, Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]
ORCHESTRATOR_DIR = REPO_ROOT / ".orchestrator"
if str(ORCHESTRATOR_DIR) not in sys.path:
    sys.path.insert(0, str(ORCHESTRATOR_DIR))

from common import validate_status_command_runtime
from rewrite.task_state_store import load_events, project_latest_state


DEFAULT_CATALOG_PATH = (
    REPO_ROOT
    / "docs"
    / "bff"
    / "execution-tasks"
    / "2026-07-26-twelve-loop-gap"
    / "tasks.json"
)
DEFAULT_PROOF_OWNERSHIP_PATH = (
    REPO_ROOT
    / "docs"
    / "bff"
    / "execution-tasks"
    / "2026-07-26-twelve-loop-gap"
    / "proof-ownership.json"
)
DEFAULT_LIVE_CONFIG_PATH = Path(
    "/home/lupin/pantheon-ci-deploy/runtime/live-supervisor-mainroot-config.json"
)
DEFAULT_COMMAND_ROOT = Path("/home/lupin/pantheon-ci-deploy/dev-root")
PROGRAM_ID = "pantheon-twelve-loop-gap-2026-07-26"
AUTO_CREATED_BY = "dispatch_twelve_loop_gap_2026_07_26"
ALLOWED_FLEET_ACTORS = {"Antigravity", "Claude", "Codex", "Codex2"}
SUPPORTED_REPOS = {"pantheon", "execute-plans"}
ALLOWED_TARGET_MATURITY = {
    "contract",
    "integrated",
    "reconciled",
    "proven-live",
    "product-level",
}
REQUIRED_NON_GOALS = {
    "No panel-only closure",
    "No seed fixture as live proof",
    "No approval gate bypass",
}
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
REQUIRED_CATALOG_FIELDS = {
    "schema_version",
    "program_id",
    "generated_at",
    "source_plan",
    "planning_addenda",
    "task_doc_contract_source",
    "external_dependencies",
    "completion_authority",
    "execution_task_counts",
    "tasks",
}
CANONICAL_LOOP_IDS = {
    "source_ingestion",
    "strategy_distillation",
    "alpha_replication",
    "persona_teaching",
    "agora_interaction_evidence",
    "human_imitation_shadow_evaluation",
    "consultation",
    "promotion_deployment",
    "capital_pool_execution",
    "telemetry_reconciliation",
    "evolution",
    "bff_health_monitoring",
}
EXPECTED_COMPLETION_AUTHORITY = {
    "schema_version": 1,
    "final_task_id": "L12-CLOSE-001",
    "guard_install_task_id": "L12-SIGNOFF-001",
    "guard_direct_dependency_ids": ["L12-FLEET-001", "PPL-ALLOC-009"],
    "final_direct_dependency_ids": [
        "L12-HOSTED-001",
        "L12-TRUTH-001",
        "L12-SIGNOFF-001",
    ],
    "required_human_ops_signoff_task_ids": ["L12-CLOSE-001"],
    "requires_protected_human_ops_verdict": True,
    "verdict_binding_fields": [
        "program_id",
        "catalog_sha256",
        "task_id",
        "closeout_manifest_sha256",
        "target_environment",
        "frontend_sha",
        "bff_sha",
        "verdict_id",
        "verifier_capability_sha256",
        "signature_algorithm",
        "key_id",
        "policy_version",
        "signature",
        "revocation_checked_at",
        "ledger_entry_id",
        "actor_id",
        "actor_role",
        "decision",
        "issued_at",
        "expires_at",
        "nonce",
    ],
}
EXPECTED_EXTERNAL_DEPENDENCIES = [
    {
        "id": "PPL-ALLOC-009",
        "required_terminal_status": "done",
        "reason": (
            "The active Human-Ops-gated allocation closeout owns the broad BFF "
            "and execute-plans src scopes; overlapping twelve-loop tasks must "
            "remain dependency-blocked until it is terminal."
        ),
        "overlap_artifacts": [
            "services/control-plane/bff",
            "execute-plans:src",
        ],
    }
]
EXPECTED_EXTERNAL_DEPENDENCY_CONSUMERS = {
    "PPL-ALLOC-009": {
        "L12-CTRL-001",
        "L12-AGORA-001",
        "L12-BFF-001",
        "L12-SIGNOFF-001",
        "L12-TRUTH-001",
        "L12-FE-TRUTH-001",
    }
}
DYNAMIC_TASK_FIELDS = {"status", "next"}
PROOF_OWNERSHIP_FIELDS = {
    "schema_version",
    "program_id",
    "base_catalog_sha256",
    "generated_at",
    "delegations",
}
PROOF_DELEGATION_FIELDS = {
    "source_task_id",
    "proof",
    "owner_task_id",
    "final_witness_task_id",
    "reason",
}


class DispatchError(RuntimeError):
    """Fail-closed catalog or live-state error."""


def canonical_json_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DispatchError(f"cannot read JSON object {path}: {type(exc).__name__}") from exc
    if not isinstance(payload, dict):
        raise DispatchError(f"JSON root must be an object: {path}")
    return payload


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


def resolve_task_state_authority(
    live_config_path: Path,
    *,
    status_root: Path,
) -> dict[str, Any]:
    """Resolve the same authoritative task journal used by the supervisor."""

    config_path = live_config_path.expanduser().absolute()
    symlink = _first_symlink_component(config_path)
    if symlink is not None or config_path.is_symlink():
        raise DispatchError(f"live config cannot contain a symlink: {symlink or config_path}")
    config = load_json_object(config_path)
    paths = config.get("paths")
    if not isinstance(paths, dict):
        raise DispatchError("live config paths must be an object")
    raw_status_file = paths.get("status_file")
    if not isinstance(raw_status_file, str) or not raw_status_file.strip():
        raise DispatchError("live config paths.status_file must be an absolute path")
    status_file = Path(os.path.expanduser(raw_status_file))
    if not status_file.is_absolute():
        raise DispatchError("live config paths.status_file must be an absolute path")
    expected_status_file = status_root / "ai-status.json"
    if status_file.resolve() != expected_status_file.resolve():
        raise DispatchError(
            "live config status authority mismatch: "
            f"{status_file.resolve()} != {expected_status_file.resolve()}"
        )

    store = config.get("task_state_store")
    if not isinstance(store, dict) or store.get("mode") != "authoritative":
        raise DispatchError("live task_state_store.mode must be authoritative")
    raw_event_log = store.get("event_log")
    if not isinstance(raw_event_log, str) or not raw_event_log.strip():
        raise DispatchError("live task_state_store.event_log must be an absolute path")
    event_log_candidate = Path(os.path.expanduser(raw_event_log))
    if not event_log_candidate.is_absolute():
        raise DispatchError("live task_state_store.event_log must be an absolute path")
    event_log = event_log_candidate.absolute()
    event_symlink = _first_symlink_component(event_log)
    if event_symlink is not None or event_log.is_symlink():
        raise DispatchError(
            f"authoritative task-state journal cannot contain a symlink: "
            f"{event_symlink or event_log}"
        )
    if not event_log.is_file() or event_log.stat().st_size == 0:
        raise DispatchError(
            f"authoritative task-state journal is missing or empty: {event_log}"
        )
    return {
        "mode": "authoritative",
        "event_log": event_log,
        "status_file": status_file.resolve(),
        "live_config": config_path,
    }


def load_authoritative_task_state(authority: dict[str, Any]) -> dict[str, Any]:
    try:
        events = load_events(Path(authority["event_log"]))
        state = project_latest_state(events)
    except (OSError, RuntimeError, ValueError) as exc:
        raise DispatchError(
            f"cannot project authoritative task-state journal: {type(exc).__name__}: {exc}"
        ) from exc
    if not isinstance(state, dict) or not isinstance(state.get("tasks"), list):
        raise DispatchError("authoritative task-state projection must contain a task list")
    return state


def _dirty_command_runtime_files(root: Path) -> list[str]:
    result = subprocess.run(
        ["git", "status", "--porcelain", "-uall"],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "git status failed").strip()
        raise DispatchError(f"cannot inspect installed command runtime: {detail}")
    dirty: list[str] = []
    for line in result.stdout.splitlines():
        path = line[3:].strip().strip("\"'")
        if " -> " in path:
            path = path.rsplit(" -> ", 1)[1].strip().strip("\"'")
        if path:
            dirty.append(path)
    return dirty


def resolve_command_runtime(
    command_root: Path,
    *,
    expected_sha: str,
) -> dict[str, str]:
    if not expected_sha.strip():
        raise DispatchError("--command-sha is required for mutation dispatch")
    root = command_root.expanduser().absolute()
    try:
        runtime = validate_status_command_runtime(
            root,
            expected_sha=expected_sha.strip(),
            expected_remote="ajoe734/pantheon",
            base_ref="origin/dev",
            require_merged=True,
        )
    except RuntimeError as exc:
        raise DispatchError(f"installed command runtime is not valid: {exc}") from exc
    dirty = _dirty_command_runtime_files(Path(runtime["root"]))
    if dirty:
        raise DispatchError(
            "installed command runtime is not fully clean: "
            + ", ".join(dirty)
        )
    script = Path(runtime["root"]) / "scripts" / "ai_status.py"
    if script.is_symlink() or not script.is_file():
        raise DispatchError(f"installed governed status command is not regular: {script}")
    return {**runtime, "script": str(script)}


def normalized_artifact(
    value: str,
    *,
    target_repo: str | None,
) -> tuple[str, str]:
    text_value = value.strip().rstrip("/")
    if text_value.startswith("execute-plans:"):
        return "execute-plans", text_value.split(":", 1)[1].lstrip("/")
    if text_value.startswith("execute-plans/"):
        return "execute-plans", text_value.removeprefix("execute-plans/")
    return target_repo or "pantheon", text_value


def task_artifact_scope(task: dict[str, Any]) -> list[tuple[str, str]]:
    artifacts = task.get("artifacts")
    if not isinstance(artifacts, list):
        return []
    target_repo = str(task.get("target_repo") or "").strip() or None
    return [
        normalized_artifact(str(value), target_repo=target_repo)
        for value in artifacts
        if isinstance(value, str) and value.strip()
    ]


def _nonempty_string(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DispatchError(f"{label} must be a non-empty string")
    return value.strip()


def _unique_string_list(
    value: Any,
    *,
    label: str,
    allow_empty: bool = False,
) -> list[str]:
    if not isinstance(value, list):
        raise DispatchError(f"{label} must be a list")
    normalized = [_nonempty_string(item, label=f"{label} item") for item in value]
    if not allow_empty and not normalized:
        raise DispatchError(f"{label} must not be empty")
    if len(normalized) != len(set(normalized)):
        raise DispatchError(f"{label} must contain unique values")
    return normalized


def _repo_relative_path(value: Any, *, label: str) -> str:
    text = _nonempty_string(value, label=label)
    path = PurePosixPath(text)
    if path.is_absolute() or ".." in path.parts or "." == text:
        raise DispatchError(f"{label} must be a scoped repository-relative path")
    return text.rstrip("/")


def artifact_overlaps(left: str, right: str) -> bool:
    left_parts = PurePosixPath(left.rstrip("/")).parts
    right_parts = PurePosixPath(right.rstrip("/")).parts
    shorter = min(len(left_parts), len(right_parts))
    return left_parts[:shorter] == right_parts[:shorter]


def _ancestors(task_id: str, by_id: dict[str, dict[str, Any]], memo: dict[str, set[str]]) -> set[str]:
    if task_id in memo:
        return memo[task_id]
    visiting: set[str] = memo.setdefault("__visiting__", set())
    if task_id in visiting:
        raise DispatchError(f"dependency cycle includes {task_id}")
    visiting.add(task_id)
    result: set[str] = set()
    for dependency in by_id[task_id]["depends_on"]:
        result.add(dependency)
        if dependency in by_id:
            result.update(_ancestors(dependency, by_id, memo))
    visiting.remove(task_id)
    memo[task_id] = result
    return result


def validate_catalog(
    catalog: dict[str, Any],
    *,
    repo_root: Path = REPO_ROOT,
) -> list[dict[str, Any]]:
    if set(catalog) != REQUIRED_CATALOG_FIELDS:
        missing = sorted(REQUIRED_CATALOG_FIELDS - set(catalog))
        extra = sorted(set(catalog) - REQUIRED_CATALOG_FIELDS)
        raise DispatchError(
            f"catalog fields are not exact: missing={missing} extra={extra}"
        )
    if catalog.get("schema_version") != 1:
        raise DispatchError("catalog schema_version must be 1")
    if catalog.get("program_id") != PROGRAM_ID:
        raise DispatchError("catalog program_id is not exact")
    _nonempty_string(catalog.get("generated_at"), label="generated_at")
    _repo_relative_path(catalog.get("source_plan"), label="source_plan")
    addenda = _unique_string_list(catalog.get("planning_addenda"), label="planning_addenda")
    for relative in [catalog["source_plan"], *addenda]:
        if not (repo_root / relative).is_file():
            raise DispatchError(f"planning source does not exist: {relative}")
    if catalog.get("task_doc_contract_source") != "tasks.json":
        raise DispatchError("tasks.json must be the task document contract source")
    if catalog.get("external_dependencies") != EXPECTED_EXTERNAL_DEPENDENCIES:
        raise DispatchError("catalog external_dependencies is not the exact live overlap contract")
    if catalog.get("completion_authority") != EXPECTED_COMPLETION_AUTHORITY:
        raise DispatchError("catalog completion_authority is not the exact protected contract")
    external_ids = {
        dependency["id"] for dependency in EXPECTED_EXTERNAL_DEPENDENCIES
    }

    tasks = catalog.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise DispatchError("catalog tasks must be a non-empty list")
    task_ids = [
        _nonempty_string(task.get("id"), label="task.id")
        for task in tasks
        if isinstance(task, dict)
    ]
    if len(task_ids) != len(tasks):
        raise DispatchError("every task must be an object")
    duplicates = sorted(task_id for task_id, count in Counter(task_ids).items() if count > 1)
    if duplicates:
        raise DispatchError("duplicate task IDs: " + ", ".join(duplicates))

    by_id: dict[str, dict[str, Any]] = {}
    for task in tasks:
        task_id = str(task["id"])
        if set(task) != REQUIRED_TASK_FIELDS:
            missing = sorted(REQUIRED_TASK_FIELDS - set(task))
            extra = sorted(set(task) - REQUIRED_TASK_FIELDS)
            raise DispatchError(f"{task_id} task fields are not exact: missing={missing} extra={extra}")
        for field in (
            "title",
            "summary_zh",
            "phase",
            "fleet_lane",
            "current_maturity",
            "next",
        ):
            _nonempty_string(task[field], label=f"{task_id}.{field}")
        if task["status"] != "todo":
            raise DispatchError(f"{task_id}.status must start as todo")
        if task["owner"] not in ALLOWED_FLEET_ACTORS or task["reviewer"] not in ALLOWED_FLEET_ACTORS:
            raise DispatchError(
                f"{task_id} owner/reviewer must be an approved fleet actor"
            )
        if task["owner"] == task["reviewer"]:
            raise DispatchError(f"{task_id} owner and reviewer must be distinct")
        if task["target_repo"] not in SUPPORTED_REPOS:
            raise DispatchError(f"{task_id}.target_repo is unsupported")
        if task["merge_target"] != "dev":
            raise DispatchError(f"{task_id}.merge_target must be dev")
        if task["target_maturity"] not in ALLOWED_TARGET_MATURITY:
            raise DispatchError(f"{task_id}.target_maturity is unsupported")
        if not isinstance(task["wave"], int) or task["wave"] < 0:
            raise DispatchError(f"{task_id}.wave must be a non-negative integer")
        if task["product_level_required"] is not True:
            raise DispatchError(f"{task_id}.product_level_required must be true")
        if not isinstance(task["requires_human_ops_signoff"], bool):
            raise DispatchError(f"{task_id}.requires_human_ops_signoff must be boolean")

        dependencies = _unique_string_list(
            task["depends_on"],
            label=f"{task_id}.depends_on",
            allow_empty=True,
        )
        list_fields = (
            "artifacts",
            "acceptance",
            "loop_ids",
            "desired_state_sources",
            "actual_state_sources",
            "proof_required",
            "non_goals",
            "dispatch_rules",
        )
        for field in list_fields:
            _unique_string_list(task[field], label=f"{task_id}.{field}")
        if not set(task["loop_ids"]).issubset(CANONICAL_LOOP_IDS):
            raise DispatchError(f"{task_id}.loop_ids contains an unknown canonical loop")
        if not REQUIRED_NON_GOALS.issubset(set(task["non_goals"])):
            raise DispatchError(f"{task_id} is missing canonical non-goals")

        artifacts = [
            _repo_relative_path(value, label=f"{task_id}.artifacts")
            for value in task["artifacts"]
        ]
        if task["target_repo"] == "execute-plans":
            if not all(path.startswith("execute-plans/") for path in artifacts):
                raise DispatchError(f"{task_id} frontend artifacts require execute-plans/ prefixes")
        elif any(path.startswith("execute-plans/") for path in artifacts):
            raise DispatchError(f"{task_id} Pantheon task contains frontend artifacts")

        evidence_root = _repo_relative_path(task["evidence_root"], label=f"{task_id}.evidence_root")
        if evidence_root not in artifacts:
            raise DispatchError(f"{task_id}.evidence_root must be a declared artifact")
        task_doc = _repo_relative_path(task["task_doc"], label=f"{task_id}.task_doc")
        if not (repo_root / task_doc).is_file():
            raise DispatchError(f"{task_id}.task_doc does not exist: {task_doc}")
        task["depends_on"] = dependencies
        task["artifacts"] = artifacts
        by_id[task_id] = task

    for task_id, task in by_id.items():
        for dependency in task["depends_on"]:
            if dependency not in by_id and dependency not in external_ids:
                raise DispatchError(f"{task_id} depends on unknown task {dependency}")
            if dependency in by_id and by_id[dependency]["wave"] > task["wave"]:
                raise DispatchError(f"{task_id} depends on later-wave task {dependency}")
    for dependency_id, expected_consumers in EXPECTED_EXTERNAL_DEPENDENCY_CONSUMERS.items():
        actual_consumers = {
            task_id
            for task_id, task in by_id.items()
            if dependency_id in task["depends_on"]
        }
        if actual_consumers != expected_consumers:
            raise DispatchError(
                f"external dependency consumers are not exact for {dependency_id}"
            )

    memo: dict[str, set[str]] = {}
    for task_id in by_id:
        _ancestors(task_id, by_id, memo)
    memo.pop("__visiting__", None)

    for index, left in enumerate(tasks):
        for right in tasks[index + 1 :]:
            if left["target_repo"] != right["target_repo"]:
                continue
            if not any(
                artifact_overlaps(left_artifact, right_artifact)
                for left_artifact in left["artifacts"]
                for right_artifact in right["artifacts"]
            ):
                continue
            left_id, right_id = str(left["id"]), str(right["id"])
            if left_id not in memo[right_id] and right_id not in memo[left_id]:
                raise DispatchError(
                    "overlapping artifact scopes require dependency order: "
                    f"{left_id} <-> {right_id}"
                )

    sinks = set(by_id)
    for task in tasks:
        sinks.difference_update(task["depends_on"])
    if sinks != {"L12-CLOSE-001"}:
        raise DispatchError("L12-CLOSE-001 must be the unique program sink")
    internal_close_ancestors = memo["L12-CLOSE-001"] & set(by_id)
    if internal_close_ancestors != set(by_id) - {"L12-CLOSE-001"}:
        raise DispatchError("every task must be an ancestor of L12-CLOSE-001")
    if set(by_id["L12-CLOSE-001"]["loop_ids"]) != CANONICAL_LOOP_IDS:
        raise DispatchError("closeout must cover the exact twelve canonical loops")
    if by_id["L12-CLOSE-001"]["requires_human_ops_signoff"] is not True:
        raise DispatchError("closeout must require Human/Ops signoff")
    authority = catalog["completion_authority"]
    final_id = authority["final_task_id"]
    guard_id = authority["guard_install_task_id"]
    if final_id not in by_id or guard_id not in by_id:
        raise DispatchError("completion authority references a missing final or guard task")
    if by_id[guard_id]["depends_on"] != authority["guard_direct_dependency_ids"]:
        raise DispatchError("completion guard direct dependency topology changed")
    if by_id[final_id]["depends_on"] != authority["final_direct_dependency_ids"]:
        raise DispatchError("final authority direct dependency topology changed")
    signoff_ids = [
        task["id"] for task in tasks if task["requires_human_ops_signoff"] is True
    ]
    if signoff_ids != authority["required_human_ops_signoff_task_ids"]:
        raise DispatchError("Human/Ops signoff task-ID authority is not exact")
    if guard_id not in memo[final_id]:
        raise DispatchError("protected completion guard must precede final authority")

    wave_counts = Counter(task["wave"] for task in tasks)
    expected_counts = {
        **{f"wave_{wave}": count for wave, count in sorted(wave_counts.items())},
        "total": len(tasks),
    }
    if catalog.get("execution_task_counts") != expected_counts:
        raise DispatchError("execution_task_counts is not exact")
    return [by_id[task_id] for task_id in task_ids]


def task_contract(task: dict[str, Any]) -> dict[str, Any]:
    return {key: task[key] for key in sorted(REQUIRED_TASK_FIELDS - DYNAMIC_TASK_FIELDS)}


def validate_proof_ownership(
    payload: dict[str, Any],
    *,
    catalog: dict[str, Any],
    tasks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Validate downstream ownership for catalog proofs without rewriting the catalog.

    The catalog remains the immutable task contract. This separately checks the
    exceptional case where a proof can only be produced by a descendant
    activation or hosted-verification task. The overlay is bound to the exact
    base catalog and may delegate proof production only forward in the DAG.
    """

    if set(payload) != PROOF_OWNERSHIP_FIELDS:
        missing = sorted(PROOF_OWNERSHIP_FIELDS - set(payload))
        extra = sorted(set(payload) - PROOF_OWNERSHIP_FIELDS)
        raise DispatchError(
            f"proof ownership fields are not exact: missing={missing} extra={extra}"
        )
    if payload.get("schema_version") != 1:
        raise DispatchError("proof ownership schema_version must be 1")
    if payload.get("program_id") != catalog.get("program_id"):
        raise DispatchError("proof ownership program_id is not exact")
    expected_catalog_sha256 = canonical_json_sha256(catalog)
    if payload.get("base_catalog_sha256") != expected_catalog_sha256:
        raise DispatchError("proof ownership base catalog digest is not exact")
    _nonempty_string(payload.get("generated_at"), label="proof ownership generated_at")

    raw_delegations = payload.get("delegations")
    if not isinstance(raw_delegations, list) or not raw_delegations:
        raise DispatchError("proof ownership delegations must be a non-empty list")

    by_id = {task["id"]: task for task in tasks}
    memo: dict[str, set[str]] = {}
    for task_id in by_id:
        _ancestors(task_id, by_id, memo)
    memo.pop("__visiting__", None)

    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for index, raw in enumerate(raw_delegations):
        if not isinstance(raw, dict) or set(raw) != PROOF_DELEGATION_FIELDS:
            raise DispatchError(
                f"proof ownership delegation {index} fields are not exact"
            )
        delegation = {
            key: _nonempty_string(
                raw.get(key),
                label=f"proof ownership delegation {index}.{key}",
            )
            for key in sorted(PROOF_DELEGATION_FIELDS)
        }
        source_id = delegation["source_task_id"]
        owner_id = delegation["owner_task_id"]
        witness_id = delegation["final_witness_task_id"]
        proof = delegation["proof"]
        if source_id not in by_id or owner_id not in by_id or witness_id not in by_id:
            raise DispatchError(
                f"proof ownership delegation {index} references an unknown task"
            )
        if proof not in by_id[source_id]["proof_required"]:
            raise DispatchError(
                f"proof ownership delegation {index} is not an exact source proof"
            )
        identity = (source_id, proof)
        if identity in seen:
            raise DispatchError(
                f"duplicate proof ownership delegation: {source_id} / {proof}"
            )
        seen.add(identity)
        if source_id not in memo[owner_id]:
            raise DispatchError(
                f"proof owner must be a descendant of source task: "
                f"{source_id} -> {owner_id}"
            )
        if owner_id != witness_id and owner_id not in memo[witness_id]:
            raise DispatchError(
                f"final witness must be the proof owner or its descendant: "
                f"{owner_id} -> {witness_id}"
            )
        normalized.append(delegation)
    return normalized


def _archive_candidates(status_root: Path, task_id: str) -> Iterable[Path]:
    archive_root = status_root / "ai-task-archive" / "tasks"
    yield archive_root / f"{task_id}.json"


def _validate_exact_archived_task(
    archive_path: Path,
    *,
    catalog: dict[str, Any],
    task: dict[str, Any],
) -> None:
    task_id = str(task["id"])
    archive = load_json_object(archive_path)
    if str(archive.get("task_id") or "").strip() != task_id:
        raise DispatchError(f"archived task identity conflicts with catalog: {task_id}")
    if str(archive.get("terminal_status") or "").strip() != "done":
        raise DispatchError(
            f"archived task is not successfully complete and cannot satisfy catalog: {task_id}"
        )
    archived_task = archive.get("task")
    if not isinstance(archived_task, dict):
        raise DispatchError(f"archived task record is malformed: {task_id}")
    if str(archived_task.get("id") or "").strip() != task_id:
        raise DispatchError(f"archived task payload conflicts with catalog: {task_id}")
    if str(archived_task.get("program_id") or "").strip() != catalog["program_id"]:
        raise DispatchError(f"archived task program conflicts with catalog: {task_id}")
    if str(archived_task.get("auto_created_by") or "").strip() != AUTO_CREATED_BY:
        raise DispatchError(f"archived task creator conflicts with catalog: {task_id}")

    expected_contract = task_contract(task)
    archived_contract = {key: archived_task.get(key) for key in expected_contract}
    if archived_contract != expected_contract:
        raise DispatchError(f"archived task contract conflicts with catalog: {task_id}")
    expected_contract_sha256 = canonical_json_sha256(expected_contract)
    if (
        str(archived_task.get("catalog_task_contract_sha256") or "").strip()
        != expected_contract_sha256
    ):
        raise DispatchError(f"archived task contract digest conflicts with catalog: {task_id}")


def plan_materialization(
    catalog: dict[str, Any],
    tasks: list[dict[str, Any]],
    *,
    status_root: Path,
    state: dict[str, Any],
) -> dict[str, Any]:
    active_tasks = state.get("tasks")
    if not isinstance(active_tasks, list):
        raise DispatchError("authoritative task-state tasks must be a list")
    active_by_id: dict[str, dict[str, Any]] = {}
    for item in active_tasks:
        if not isinstance(item, dict):
            raise DispatchError("ai-status.json task entries must be objects")
        task_id = str(item.get("id") or "").strip()
        if task_id:
            if task_id in active_by_id:
                raise DispatchError(f"duplicate active task ID: {task_id}")
            active_by_id[task_id] = item

    external_state: dict[str, dict[str, Any]] = {}
    for dependency in EXPECTED_EXTERNAL_DEPENDENCIES:
        dependency_id = dependency["id"]
        active = active_by_id.get(dependency_id)
        if active is not None:
            terminal_status = str(active.get("status") or "").strip()
            external_state[dependency_id] = {
                "source": "active",
                "status": terminal_status,
                "satisfied": terminal_status == dependency["required_terminal_status"],
            }
            continue
        archive_path = next(
            (
                path
                for path in _archive_candidates(status_root, dependency_id)
                if path.is_file()
            ),
            None,
        )
        if archive_path is None:
            raise DispatchError(
                f"external dependency is absent from active and archive truth: {dependency_id}"
            )
        archive = load_json_object(archive_path)
        terminal_status = str(archive.get("terminal_status") or "").strip()
        external_state[dependency_id] = {
            "source": "archive",
            "status": terminal_status,
            "satisfied": terminal_status == dependency["required_terminal_status"],
        }

    external_ids = set(external_state)
    catalog_by_id = {task["id"]: task for task in tasks}
    catalog_ancestor_memo: dict[str, set[str]] = {}
    for task_id in catalog_by_id:
        _ancestors(task_id, catalog_by_id, catalog_ancestor_memo)
    catalog_ancestor_memo.pop("__visiting__", None)
    for task in tasks:
        catalog_scope = task_artifact_scope(task)
        for active_id, active in active_by_id.items():
            if active_id in {task["id"]} or str(active.get("status") or "") in {
                "done",
                "cancelled",
                "superseded",
            }:
                continue
            active_scope = task_artifact_scope(active)
            overlap = any(
                left_repo == right_repo and artifact_overlaps(left_path, right_path)
                for left_repo, left_path in catalog_scope
                for right_repo, right_path in active_scope
            )
            if not overlap:
                continue
            if active_id in external_ids and active_id in task["depends_on"]:
                continue
            if active_id in catalog_by_id and (
                active_id in catalog_ancestor_memo[task["id"]]
                or task["id"] in catalog_ancestor_memo[active_id]
            ):
                continue
            raise DispatchError(
                f"live nonterminal artifact overlap is not dependency-ordered: "
                f"{task['id']} <-> {active_id}"
            )

    create: list[dict[str, Any]] = []
    exact: list[str] = []
    for task in tasks:
        task_id = str(task["id"])
        archive_path = next(
            (
                path
                for path in _archive_candidates(status_root, task_id)
                if path.is_file()
            ),
            None,
        )
        if archive_path is not None:
            if task_id in active_by_id:
                raise DispatchError(
                    f"task ID is present in both active and archive truth: {task_id}"
                )
            _validate_exact_archived_task(
                archive_path,
                catalog=catalog,
                task=task,
            )
            exact.append(task_id)
            continue
        active = active_by_id.get(task_id)
        if active is None:
            create.append(task)
            continue
        active_contract = {key: active.get(key) for key in task_contract(task)}
        if active_contract != task_contract(task):
            raise DispatchError(f"active task contract conflicts with catalog: {task_id}")
        exact.append(task_id)
    return {
        "program_id": catalog["program_id"],
        "catalog_sha256": canonical_json_sha256(catalog),
        "status_root": str(status_root),
        "external_dependencies": external_state,
        "create": [task["id"] for task in create],
        "exact": exact,
        "create_tasks": create,
    }


def artifact_conflict_guard(
    task: dict[str, Any],
    *,
    catalog: dict[str, Any],
    tasks: list[dict[str, Any]],
) -> dict[str, Any]:
    by_id = {item["id"]: item for item in tasks}
    memo: dict[str, set[str]] = {}
    for task_id in by_id:
        _ancestors(task_id, by_id, memo)
    memo.pop("__visiting__", None)
    task_scope = task_artifact_scope(task)
    allowed: set[str] = set()
    for other in tasks:
        if other["id"] == task["id"]:
            continue
        overlap = any(
            left_repo == right_repo and artifact_overlaps(left_path, right_path)
            for left_repo, left_path in task_scope
            for right_repo, right_path in task_artifact_scope(other)
        )
        if overlap and (
            other["id"] in memo[task["id"]]
            or task["id"] in memo[other["id"]]
        ):
            allowed.add(other["id"])
    external_ids = {
        dependency["id"] for dependency in EXPECTED_EXTERNAL_DEPENDENCIES
    }
    allowed.update(external_ids & set(task["depends_on"]))
    return {
        "schema_version": 1,
        "program_id": PROGRAM_ID,
        "catalog_sha256": canonical_json_sha256(catalog),
        "task_id": task["id"],
        "artifact_scope": [
            {"repo": repo, "path": path}
            for repo, path in sorted(task_scope)
        ],
        "allowed_overlap_task_ids": sorted(allowed),
    }


def assignment_environment(
    task: dict[str, Any],
    *,
    catalog: dict[str, Any],
    tasks: list[dict[str, Any]],
) -> dict[str, str]:
    metadata = {
        key: value
        for key, value in task.items()
        if key not in {"id", "title", "owner", "reviewer", "status", "next"}
    }
    metadata.update(
        {
            "program_id": PROGRAM_ID,
            "catalog_task_contract_sha256": canonical_json_sha256(task_contract(task)),
            "artifact_conflict_guard": artifact_conflict_guard(
                task,
                catalog=catalog,
                tasks=tasks,
            ),
            "auto_created_by": AUTO_CREATED_BY,
            "mutates_canonical": True,
        }
    )
    return {
        "TASK_TITLE": task["title"],
        "TASK_SUMMARY_ZH": task["summary_zh"],
        "TASK_PHASE": task["phase"],
        "TASK_DEPENDS_ON": ",".join(task["depends_on"]),
        "TASK_ARTIFACTS": ",".join(task["artifacts"]),
        "TASK_ACCEPTANCE": "Catalog acceptance contract applies",
        "TASK_NEXT": task["next"],
        "TASK_METADATA_JSON": json.dumps(metadata, separators=(",", ":"), ensure_ascii=False),
        "TASK_AUTO_CREATED_BY": AUTO_CREATED_BY,
        "TASK_MUTATES_CANONICAL": "true",
    }


def apply_materialization(
    plan: dict[str, Any],
    *,
    catalog: dict[str, Any],
    tasks: list[dict[str, Any]],
    status_root: Path,
    authority: dict[str, Any],
    command_runtime: dict[str, str],
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    verify_after_write: bool = True,
) -> list[str]:
    actor = os.environ.get("AI_NAME", "").strip()
    if actor not in {"Human", "Human/Ops", "human", "human/ops", "ops"}:
        raise DispatchError("--apply requires AI_NAME=Human/Ops for governed catalog materialization")
    command_root = Path(command_runtime["root"])
    source_sha = command_runtime["source_sha"]
    created: list[str] = []
    for task in plan["create_tasks"]:
        env = {
            **os.environ,
            **assignment_environment(task, catalog=catalog, tasks=tasks),
            "PANTHEON_STATUS_ROOT": str(status_root),
            "PANTHEON_TASK_STATE_STORE_MODE": str(authority["mode"]),
            "PANTHEON_TASK_STATE_EVENT_LOG": str(authority["event_log"]),
            "PANTHEON_COMMAND_ROOT": str(command_root),
            "PANTHEON_COMMAND_RUNTIME_SHA": source_sha,
            "PANTHEON_COMMAND_REMOTE": "ajoe734/pantheon",
            "PANTHEON_COMMAND_BASE_REF": "origin/dev",
            "TASK_ASSIGN_CREATE_ONLY": "true",
            "AI_NAME": "Human/Ops",
        }
        result = runner(
            [
                sys.executable,
                command_runtime["script"],
                "assign",
                task["id"],
                task["owner"],
                task["reviewer"],
                task["title"],
            ],
            cwd=str(command_root),
            env=env,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            raise DispatchError(f"materialization failed for {task['id']}: {detail}")
        if verify_after_write:
            current = load_authoritative_task_state(authority)
            active = next(
                (
                    item
                    for item in current["tasks"]
                    if isinstance(item, dict) and item.get("id") == task["id"]
                ),
                None,
            )
            if active is None:
                raise DispatchError(
                    f"authoritative readback is missing materialized task {task['id']}"
                )
            active_contract = {key: active.get(key) for key in task_contract(task)}
            if active_contract != task_contract(task):
                raise DispatchError(
                    f"authoritative readback contract mismatch for {task['id']}"
                )
            if active.get("next") != task["next"]:
                raise DispatchError(
                    f"authoritative readback next instruction mismatch for {task['id']}"
                )
            plan_materialization(
                catalog,
                tasks,
                status_root=status_root,
                state=current,
            )
        created.append(task["id"])
    return created


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--validate-only", action="store_true")
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--catalog", default=str(DEFAULT_CATALOG_PATH))
    parser.add_argument(
        "--proof-ownership",
        default=str(DEFAULT_PROOF_OWNERSHIP_PATH),
    )
    parser.add_argument("--live-config", default=str(DEFAULT_LIVE_CONFIG_PATH))
    parser.add_argument("--command-root", default=str(DEFAULT_COMMAND_ROOT))
    parser.add_argument(
        "--command-sha",
        default=str(os.environ.get("PANTHEON_COMMAND_RUNTIME_SHA") or ""),
    )
    args = parser.parse_args(argv)

    catalog_path = Path(args.catalog).resolve()
    catalog = load_json_object(catalog_path)
    tasks = validate_catalog(catalog)
    proof_ownership_path = Path(args.proof_ownership).resolve()
    proof_ownership = load_json_object(proof_ownership_path)
    delegations = validate_proof_ownership(
        proof_ownership,
        catalog=catalog,
        tasks=tasks,
    )
    proof_ownership_sha256 = hashlib.sha256(
        proof_ownership_path.read_bytes()
    ).hexdigest()
    if args.validate_only:
        print(
            json.dumps(
                {
                    "status": "valid",
                    "program_id": catalog["program_id"],
                    "task_count": len(tasks),
                    "catalog_sha256": canonical_json_sha256(catalog),
                    "proof_delegation_count": len(delegations),
                    "proof_ownership_sha256": proof_ownership_sha256,
                },
                sort_keys=True,
            )
        )
        return 0

    status_root = Path(
        os.path.expanduser(os.environ.get("PANTHEON_STATUS_ROOT", str(REPO_ROOT)))
    ).resolve()
    authority = resolve_task_state_authority(
        Path(args.live_config),
        status_root=status_root,
    )
    state = load_authoritative_task_state(authority)
    plan = plan_materialization(
        catalog,
        tasks,
        status_root=status_root,
        state=state,
    )
    if args.dry_run:
        output = {key: value for key, value in plan.items() if key != "create_tasks"}
        output["proof_ownership_sha256"] = proof_ownership_sha256
        output["proof_delegation_count"] = len(delegations)
        output["task_state_store"] = {
            "mode": authority["mode"],
            "event_log": str(authority["event_log"]),
        }
        output["status"] = "dry_run"
        print(json.dumps(output, indent=2, sort_keys=True))
        return 0

    command_runtime = resolve_command_runtime(
        Path(args.command_root),
        expected_sha=args.command_sha,
    )
    if Path(command_runtime["root"]).resolve() != REPO_ROOT.resolve():
        raise DispatchError(
            "--apply must execute the dispatcher and catalog from the exact "
            "installed command root"
        )
    installed_catalog = (
        Path(command_runtime["root"])
        / "docs"
        / "bff"
        / "execution-tasks"
        / "2026-07-26-twelve-loop-gap"
        / "tasks.json"
    ).resolve()
    if catalog_path != installed_catalog:
        raise DispatchError(
            f"--apply catalog must be the installed reviewed catalog: "
            f"{catalog_path} != {installed_catalog}"
        )
    installed_proof_ownership = (
        Path(command_runtime["root"])
        / "docs"
        / "bff"
        / "execution-tasks"
        / "2026-07-26-twelve-loop-gap"
        / "proof-ownership.json"
    ).resolve()
    if proof_ownership_path != installed_proof_ownership:
        raise DispatchError(
            "--apply proof ownership must be the installed reviewed overlay: "
            f"{proof_ownership_path} != {installed_proof_ownership}"
        )
    created = apply_materialization(
        plan,
        catalog=catalog,
        tasks=tasks,
        status_root=status_root,
        authority=authority,
        command_runtime=command_runtime,
    )
    print(
        json.dumps(
            {
                "status": "applied",
                "program_id": catalog["program_id"],
                "catalog_sha256": plan["catalog_sha256"],
                "created": created,
                "exact": plan["exact"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except DispatchError as exc:
        print(f"dispatch failed closed: {exc}", file=sys.stderr)
        raise SystemExit(2)
