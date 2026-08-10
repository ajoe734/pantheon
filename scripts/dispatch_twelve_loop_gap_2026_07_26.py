#!/usr/bin/env python3
"""Validate and materialize the 2026-07-26 twelve-loop remediation DAG.

The catalog is validated as one immutable graph before any live mutation. The
legacy profile retains its canonical ``scripts/ai_status.py assign`` workflow;
the explicit current-proof profile stages its G1 frontier in memory and commits
one authoritative task-state transaction. Exact active or successfully archived
catalog tasks are skipped; malformed, non-successful, or conflicting IDs fail
closed. This avoids the DevTaskPacket bulk delimiter/partial-replay limitation
while preserving the repository task-state locks and audit log.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import importlib.util
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

from common import durable_write_bytes, validate_status_command_runtime
from rewrite.task_state_store import load_snapshot


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
CURRENT_CATALOG_RELATIVE_PATH = (
    "docs/bff/execution-tasks/"
    "2026-08-03-l12-guarded-remediation-correction/"
    "corrected-remediation-tasks.json"
)
DEFAULT_CURRENT_CATALOG_PATH = REPO_ROOT / CURRENT_CATALOG_RELATIVE_PATH
PREVIOUS_CURRENT_CATALOG_RELATIVE_PATH = (
    "docs/bff/execution-tasks/"
    "2026-07-31-l12-current-gap-supervisor-dispatch/"
    "guarded-remediation-tasks.json"
)
DEFAULT_PREVIOUS_CURRENT_CATALOG_PATH = (
    REPO_ROOT / PREVIOUS_CURRENT_CATALOG_RELATIVE_PATH
)
DEFAULT_LIVE_CONFIG_PATH = Path(
    "/home/lupin/pantheon-ci-deploy/runtime/live-supervisor-mainroot-config.json"
)
DEFAULT_COMMAND_ROOT = Path("/home/lupin/pantheon-ci-deploy/dev-root")
PROGRAM_ID = "pantheon-twelve-loop-gap-2026-07-26"
CURRENT_PROGRAM_ID = (
    "pantheon-twelve-loop-gap-corrected-remediation-2026-08-03"
)
PREVIOUS_CURRENT_PROGRAM_ID = (
    "pantheon-twelve-loop-gap-current-proof-remediation-2026-07-31"
)
AUTO_CREATED_BY = "dispatch_twelve_loop_gap_2026_07_26"
ALLOWED_FLEET_ACTORS = {"Antigravity", "Claude", "Codex", "Codex2"}
CURRENT_ALLOWED_FLEET_ACTORS = {
    "Antigravity",
    "Claude2",
    "Claude",
    "Codex2",
    "Codex",
}
SUPPORTED_REPOS = {"pantheon", "execute-plans"}
ALLOWED_TARGET_MATURITY = {
    "contract",
    "integrated",
    "reconciled",
    "proven-live",
    "product-level",
    "implemented",
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
CURRENT_REQUIRED_CATALOG_FIELDS = {
    "schema_version",
    "program_id",
    "generated_at",
    "source_audit",
    "verified_baseline",
    "dispatch_contract",
    "completion_authority",
    "execution_task_counts",
    "tasks",
}
CURRENT_REQUIRED_TASK_FIELDS = REQUIRED_TASK_FIELDS | {
    "owner_preference",
    "reviewer_preference",
}
CURRENT_DYNAMIC_TASK_FIELDS = {"status", "next", "owner", "reviewer"}
CURRENT_CATALOG_FILE_SHA256 = (
    "5ab2efe3cb55673366994b759f3d52190573ca9f0fc002393da3f5449b59e45b"
)
CURRENT_CATALOG_CANONICAL_SHA256 = (
    "247c87fab74c5d7dc2eaaefa03b76ed18919b1d8ca2465536718439c01e565e9"
)
CURRENT_SOURCE_PR = 4539
CURRENT_SOURCE_HEAD = "f2b48094226f56a392f33a3f65d7a5118dca37a1"
CURRENT_SOURCE_BRANCH_CI_RUN = 30882135477
PREVIOUS_CURRENT_CATALOG_FILE_SHA256 = (
    "7f67b32555341de19feaa46b98fd09ad69de2a5b2f6767c40287626d9c01fdca"
)
PREVIOUS_CURRENT_CATALOG_CANONICAL_SHA256 = (
    "6adf2d2e987d8ebed96689e35db346e9f4eacb3d63a0b635bf8a51426f9ce02f"
)
PREVIOUS_CURRENT_SOURCE_PR = 4394
PREVIOUS_CURRENT_SOURCE_HEAD = "fb9adfb84944e276b254ccfdfff784fb6728a7f4"
PREVIOUS_CURRENT_SOURCE_BRANCH_CI_RUN = 30635898120
PREVIOUS_HELD_CLOSE_RELEASE_ORDER_CONTRACT_SHA256 = {
    "L12-CONTROLLER-CATALOG-INTEGRATION-20260731": (
        "c9b5a5e7c955b0958f514efec9aeae72538541b4b091b7ce3cd47f6dbd2b9388"
    ),
    "L12-CURRENT-PROOF-RELEASE-GATE-20260731": (
        "3dfcfd78246e800716b7cc97d25ab6a8f0831c64ac63d6436bcb6fe0cfacd943"
    ),
    "L12-VERIFY-LEARN-REAL-VERIFIER-001": (
        "ba59f566dbb0eee2a914b6b428dc96eb564dbe7d57d125b7009b532f3f624e13"
    ),
}
HELD_CLOSE_TASK_ID = "L12-CLOSE-001"
HELD_CLOSE_REGISTRY_ARTIFACT = "docs/deployment/loop-catalog.registry.json"
HELD_CLOSE_CATALOG_TASK_CONTRACT_SHA256 = (
    "807aa54dfff9f9132c974964c0fe8cf0851b6dd9fa11243d21ad48a9c70d9e64"
)
HELD_CLOSE_DEPENDENCIES = [
    "L12-HOSTED-001",
    "L12-TRUTH-001",
    "L12-SIGNOFF-001",
]
HELD_CLOSE_ARTIFACTS = [
    HELD_CLOSE_REGISTRY_ARTIFACT,
    "docs/04/pantheon_twelve_loop_gap_2026-07-26",
    "docs/deployment/evidence/twelve-loop-gap/L12-CLOSE-001",
]
HELD_CLOSE_ARTIFACT_CONFLICT_GUARD = {
    "schema_version": 1,
    "program_id": PROGRAM_ID,
    "catalog_sha256": (
        "8c7610b0e6bbba31c36cb0ecd1ddce4bf843fc6de89dcaecc4a5e3154af8933d"
    ),
    "task_id": HELD_CLOSE_TASK_ID,
    "artifact_scope": [
        {
            "repo": "pantheon",
            "path": "docs/04/pantheon_twelve_loop_gap_2026-07-26",
        },
        {
            "repo": "pantheon",
            "path": "docs/deployment/evidence/twelve-loop-gap/L12-CLOSE-001",
        },
        {"repo": "pantheon", "path": HELD_CLOSE_REGISTRY_ARTIFACT},
    ],
    "allowed_overlap_task_ids": ["L12-TRUTH-001"],
}
CURRENT_OWNER_PREFERENCE = [
    "Antigravity",
    "Claude2",
    "Claude",
    "Codex2",
    "Codex",
]
CURRENT_REVIEWER_PREFERENCE = [
    "Claude2",
    "Antigravity",
    "Codex2",
    "Codex",
]
CURRENT_ASSIGNMENT_RESOLUTION_FIELDS = {
    "schema_version",
    "source",
    "readiness_sha256",
    "observed_at",
    "catalog_owner",
    "catalog_reviewer",
    "owner",
    "reviewer",
    "owner_evaluations",
    "reviewer_evaluations",
    "owner_fallbacks",
    "reviewer_fallbacks",
}
CURRENT_ASSIGNMENT_EVALUATION_FIELDS = {
    "agent",
    "ready",
    "reasons",
    "selected",
    "considered",
}
CURRENT_TASK_IDS = [
    "L12-CONTROLLER-TEACH-20260731",
    "L12-CONTROLLER-AGORA-20260731",
    "L12-CONTROLLER-IMIT-20260731",
    "L12-CONTROLLER-CONS-20260731",
    "L12-CONTROLLER-DEP-20260731",
    "L12-CONTROLLER-CAP-20260731",
    "L12-CONTROLLER-TELREC-20260731",
    "L12-CONTROLLER-EVO-20260731",
    "L12-CONTROLLER-BFF-20260731",
    "L12-EVIDENCE-REVALIDATE-FLEET-20260731",
    "L12-EVIDENCE-REVALIDATE-CTRL-20260731",
    "L12-EVIDENCE-REVALIDATE-TEL-20260731",
    "L12-EVIDENCE-REVALIDATE-REC-20260731",
    "L12-EVIDENCE-REVALIDATE-SRC-20260731",
    "L12-EVIDENCE-REVALIDATE-ALPHA-20260731",
    "L12-EVIDENCE-REVALIDATE-AGORA-20260731",
    "L12-EVIDENCE-REVALIDATE-CONS-20260731",
    "L12-EVIDENCE-REVALIDATE-DEP-20260731",
    "L12-EVIDENCE-REVALIDATE-TEACH-20260731",
    "L12-EVIDENCE-REVALIDATE-IMIT-20260731",
    "L12-EVIDENCE-REVALIDATE-CAP-20260731",
    "L12-EVIDENCE-REVALIDATE-EVO-20260731",
    "L12-EVIDENCE-REVALIDATE-BFF-20260731",
    "L12-EVIDENCE-REVALIDATE-SIGNOFF-20260731",
    "L12-EVIDENCE-REVALIDATE-TRUTH-20260731",
    "L12-VERIFY-LEARN-REAL-VERIFIER-001",
    "L12-CONTROLLER-CATALOG-INTEGRATION-20260731",
    "L12-CURRENT-PROOF-RELEASE-GATE-20260731",
]
BASE_CURRENT_EXTERNAL_DEPENDENCY_IDS = {
    "L12-AGORA-001",
    "L12-ALPHA-001",
    "L12-BFF-001",
    "L12-CAP-001",
    "L12-CONS-001",
    "L12-CTRL-001",
    "L12-DEP-001",
    "L12-EVO-001",
    "L12-FLEET-001",
    "L12-IMIT-001",
    "L12-REC-001",
    "L12-SIGNOFF-001",
    "L12-SRC-001",
    "L12-TEACH-001",
    "L12-TEL-001",
    "L12-TRUTH-001",
    "SUP-PREEMPTION-DISPATCH-ELIGIBILITY-20260731",
}
CURRENT_EXTERNAL_DEPENDENCY_IDS = BASE_CURRENT_EXTERNAL_DEPENDENCY_IDS | {
    "LIFECYCLE-PROJ-BFF-001",
    "LIFECYCLE-PROJ-RETIRE-001",
}
PREVIOUS_CURRENT_EXTERNAL_DEPENDENCY_IDS = BASE_CURRENT_EXTERNAL_DEPENDENCY_IDS
CURRENT_RUNTIME_GATE_IDS = {
    "SUP-SEEN-EVENT-KEYS-NONNULL-20260731",
    "SUP-PREEMPTION-DISPATCH-ELIGIBILITY-20260731",
}
CURRENT_LOOP_IDS = {
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
    "program-evidence",
}
CURRENT_EXPECTED_DISPATCH_CONTRACT = {
    "bootstrap_task_id": "SUP-L12-GUARDED-REMEDIATION-CATALOG-CORRECTION-20260803",
    "dispatcher": "scripts/dispatch_twelve_loop_gap_2026_07_26.py",
    "mode": "program-specific-guarded-dispatch",
    "generic_dev_task_packet_for_product_tasks": False,
    "required_runtime_gates": [
        "SUP-SEEN-EVENT-KEYS-NONNULL-20260731",
        "SUP-PREEMPTION-DISPATCH-ELIGIBILITY-20260731",
    ],
    "materialize_only_after_dispatcher_merge_and_live_promotion": True,
}
PREVIOUS_CURRENT_EXPECTED_DISPATCH_CONTRACT = {
    **CURRENT_EXPECTED_DISPATCH_CONTRACT,
    "bootstrap_task_id": "SUP-L12-GUARDED-REMEDIATION-DISPATCHER-20260731",
}
CURRENT_EXPECTED_COMPLETION_AUTHORITY = {
    "final_task_id": "L12-CLOSE-001",
    "current_proof_release_gate_id": "L12-CURRENT-PROOF-RELEASE-GATE-20260731",
    "hosted_task_id": "L12-HOSTED-001",
    "requires_protected_human_ops_verdict": True,
}
CURRENT_EXPECTED_EXECUTION_COUNTS = {
    "controller_parallel": 9,
    "evidence_revalidation_parallel": 16,
    "shared_integration": 1,
    "release_gate": 1,
    "learning_verifier_rebuild": 1,
    "total_new_product_tasks": 28,
    "maximum_parallel_frontier_G1": 25,
}
CURRENT_EXPECTED_BASELINE = {
    "canonical_loop_count": 12,
    "implemented_controller_contract_count": 3,
    "missing_controller_contract_count": 9,
    "original_archived_done_evidence_count": 18,
    "current_validator_and_checksum_pass_count": 3,
    "current_revalidation_required_count": 16,
    "both_pass_task_ids": ["L12-DIST-001", "L12-BFF-001", "L12-MANIFEST-001"],
    "loop_defect_categorization": {
      "missing_runtime_binding_only": [
        "persona_teaching",
        "human_imitation_shadow_evaluation",
        "consultation",
        "bff_health_monitoring"
      ],
      "truly_missing_end_to_end": [
        "agora_interaction_evidence",
        "promotion_deployment"
      ],
      "adjacent_component_exists_missing_specific_piece": [
        "capital_pool_execution",
        "evolution",
        "telemetry_reconciliation"
      ]
    }
}
PREVIOUS_CURRENT_EXPECTED_BASELINE = {
    "canonical_loop_count": 12,
    "implemented_controller_contract_count": 3,
    "missing_controller_contract_count": 9,
    "original_archived_done_evidence_count": 18,
    "current_validator_and_checksum_pass_count": 2,
    "current_revalidation_required_count": 16,
    "both_pass_task_ids": ["L12-DIST-001", "L12-MANIFEST-001"],
}
CURRENT_PROFILE_BY_PROGRAM_ID = {
    CURRENT_PROGRAM_ID: {
        "catalog_relative_path": CURRENT_CATALOG_RELATIVE_PATH,
        "catalog_file_sha256": CURRENT_CATALOG_FILE_SHA256,
        "catalog_canonical_sha256": CURRENT_CATALOG_CANONICAL_SHA256,
        "source_pr": CURRENT_SOURCE_PR,
        "source_head": CURRENT_SOURCE_HEAD,
        "source_branch_ci_run": CURRENT_SOURCE_BRANCH_CI_RUN,
        "source_audits": {
            "docs/reviews/archive/2026-08-01-l12-current-three-pass-gap-reaudit.md"
        },
        "expected_baseline": CURRENT_EXPECTED_BASELINE,
        "expected_dispatch_contract": CURRENT_EXPECTED_DISPATCH_CONTRACT,
        "external_dependency_ids": CURRENT_EXTERNAL_DEPENDENCY_IDS,
    },
    PREVIOUS_CURRENT_PROGRAM_ID: {
        "catalog_relative_path": PREVIOUS_CURRENT_CATALOG_RELATIVE_PATH,
        "catalog_file_sha256": PREVIOUS_CURRENT_CATALOG_FILE_SHA256,
        "catalog_canonical_sha256": PREVIOUS_CURRENT_CATALOG_CANONICAL_SHA256,
        "source_pr": PREVIOUS_CURRENT_SOURCE_PR,
        "source_head": PREVIOUS_CURRENT_SOURCE_HEAD,
        "source_branch_ci_run": PREVIOUS_CURRENT_SOURCE_BRANCH_CI_RUN,
        "source_audits": {
            "docs/04/pantheon_twelve_loop_gap_2026-07-26/archive/"
            "CURRENT_THREE_PASS_GAP_AUDIT_2026-07-31T0640Z.md"
        },
        "expected_baseline": PREVIOUS_CURRENT_EXPECTED_BASELINE,
        "expected_dispatch_contract": PREVIOUS_CURRENT_EXPECTED_DISPATCH_CONTRACT,
        "external_dependency_ids": PREVIOUS_CURRENT_EXTERNAL_DEPENDENCY_IDS,
    },
}


def _current_profile(catalog: dict[str, Any]) -> dict[str, Any] | None:
    return CURRENT_PROFILE_BY_PROGRAM_ID.get(str(catalog.get("program_id") or ""))


def _is_current_catalog(catalog: dict[str, Any]) -> bool:
    return _current_profile(catalog) is not None
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


def load_authoritative_task_snapshot(
    authority: dict[str, Any],
    *,
    refresh_checkpoint: bool = True,
) -> dict[str, Any]:
    """Return one validated authoritative journal generation.

    ``load_snapshot`` verifies the complete journal prefix digest while reusing
    the checkpoint's already-validated head and parsing only an appended tail.
    Keeping the snapshot intact also binds the projected state, event identity,
    and scale telemetry to the same shared-lock window.
    """

    try:
        snapshot = load_snapshot(
            Path(authority["event_log"]),
            refresh_checkpoint=refresh_checkpoint,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise DispatchError(
            f"cannot project authoritative task-state journal: {type(exc).__name__}: {exc}"
        ) from exc
    state = snapshot.get("state")
    if not isinstance(state, dict) or not isinstance(state.get("tasks"), list):
        raise DispatchError("authoritative task-state projection must contain a task list")
    return snapshot


def load_authoritative_task_state(authority: dict[str, Any]) -> dict[str, Any]:
    """Compatibility projection backed by one authoritative snapshot read."""

    return load_authoritative_task_snapshot(authority)["state"]


def authoritative_snapshot_evidence(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Expose non-payload snapshot facts for dry-run and admission evidence."""

    return {
        "event_count": int(snapshot["event_count"]),
        "byte_size": int(snapshot["byte_size"]),
        "last_event_id": snapshot["last_event_id"],
        "last_event_sha256": snapshot["last_event_sha256"],
        "state_sha256": snapshot["state_sha256"],
        "checkpoint_used": snapshot["resumed_from_checkpoint"] is True,
        "revalidated_tail_events": int(snapshot["revalidated_events"]),
    }


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


def _validate_legacy_catalog(
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


def _validate_current_catalog(catalog: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate a reviewed current-proof graph without normalizing it."""

    profile = _current_profile(catalog)
    if profile is None:
        raise DispatchError("current catalog program_id is not exact")

    if set(catalog) != CURRENT_REQUIRED_CATALOG_FIELDS:
        missing = sorted(CURRENT_REQUIRED_CATALOG_FIELDS - set(catalog))
        extra = sorted(set(catalog) - CURRENT_REQUIRED_CATALOG_FIELDS)
        raise DispatchError(
            f"current catalog fields are not exact: missing={missing} extra={extra}"
        )
    if catalog.get("schema_version") != 1:
        raise DispatchError("current catalog schema_version must be 1")
    _nonempty_string(catalog.get("generated_at"), label="generated_at")
    if catalog.get("source_audit") not in profile["source_audits"]:
        raise DispatchError("current catalog source_audit is not exact")
    if catalog.get("verified_baseline") != profile["expected_baseline"]:
        raise DispatchError("current catalog verified_baseline is not exact")
    if catalog.get("dispatch_contract") != profile["expected_dispatch_contract"]:
        raise DispatchError("current catalog dispatch_contract is not exact")
    if catalog.get("completion_authority") != CURRENT_EXPECTED_COMPLETION_AUTHORITY:
        raise DispatchError("current catalog completion_authority is not exact")
    if catalog.get("execution_task_counts") != CURRENT_EXPECTED_EXECUTION_COUNTS:
        raise DispatchError("current catalog execution_task_counts is not exact")
    tasks = catalog.get("tasks")
    if not isinstance(tasks, list) or len(tasks) != 28:
        raise DispatchError("current catalog must contain exactly 28 tasks")
    if any(not isinstance(task, dict) for task in tasks):
        raise DispatchError("every current catalog task must be an object")
    task_ids = [
        _nonempty_string(task.get("id"), label="task.id")
        for task in tasks
    ]
    duplicates = sorted(
        task_id for task_id, count in Counter(task_ids).items() if count > 1
    )
    if duplicates:
        raise DispatchError("duplicate task IDs: " + ", ".join(duplicates))
    if task_ids != CURRENT_TASK_IDS:
        raise DispatchError("current catalog task IDs/order are not exact")

    wave_order = {"G1": 1, "G2": 2, "G3": 3}
    by_id: dict[str, dict[str, Any]] = {}
    observed_external_ids: set[str] = set()
    for task in tasks:
        task_id = str(task["id"])
        if set(task) != CURRENT_REQUIRED_TASK_FIELDS:
            missing = sorted(CURRENT_REQUIRED_TASK_FIELDS - set(task))
            extra = sorted(set(task) - CURRENT_REQUIRED_TASK_FIELDS)
            raise DispatchError(
                f"{task_id} current task fields are not exact: "
                f"missing={missing} extra={extra}"
            )
        for field in (
            "title",
            "summary_zh",
            "phase",
            "fleet_lane",
            "current_maturity",
            "target_maturity",
            "next",
        ):
            _nonempty_string(task[field], label=f"{task_id}.{field}")
        if task["status"] != "todo":
            raise DispatchError(f"{task_id}.status must start as todo")
        if task["owner"] not in CURRENT_ALLOWED_FLEET_ACTORS:
            raise DispatchError(f"{task_id}.owner is not an approved fleet actor")
        if task["reviewer"] not in CURRENT_ALLOWED_FLEET_ACTORS:
            raise DispatchError(f"{task_id}.reviewer is not an approved fleet actor")
        if task["owner"] == task["reviewer"]:
            raise DispatchError(f"{task_id} owner and reviewer must be distinct")
        if task["owner_preference"] != CURRENT_OWNER_PREFERENCE:
            raise DispatchError(f"{task_id}.owner_preference is not exact")
        if task["reviewer_preference"] != CURRENT_REVIEWER_PREFERENCE:
            raise DispatchError(f"{task_id}.reviewer_preference is not exact")
        if task["wave"] not in wave_order:
            raise DispatchError(f"{task_id}.wave is unsupported")
        if task["target_repo"] != "pantheon" or task["merge_target"] != "dev":
            raise DispatchError(f"{task_id} repository/merge target is not exact")
        if task["product_level_required"] is not True:
            raise DispatchError(f"{task_id}.product_level_required must be true")
        if task["requires_human_ops_signoff"] is not False:
            raise DispatchError(
                f"{task_id} cannot claim protected Human/Ops signoff authority"
            )

        dependencies = _unique_string_list(
            task["depends_on"],
            label=f"{task_id}.depends_on",
            allow_empty=True,
        )
        for field in (
            "artifacts",
            "acceptance",
            "loop_ids",
            "desired_state_sources",
            "actual_state_sources",
            "proof_required",
            "non_goals",
            "dispatch_rules",
        ):
            _unique_string_list(task[field], label=f"{task_id}.{field}")
        if not set(task["loop_ids"]).issubset(CURRENT_LOOP_IDS):
            raise DispatchError(f"{task_id}.loop_ids contains an unknown loop")
        if not REQUIRED_NON_GOALS.issubset(set(task["non_goals"])):
            raise DispatchError(f"{task_id} is missing canonical non-goals")
        if "No live-capital activation" not in task["non_goals"]:
            raise DispatchError(f"{task_id} is missing the live-capital non-goal")
        if "No .orchestrator/config.json edit" not in task["non_goals"]:
            raise DispatchError(f"{task_id} is missing the config-edit non-goal")

        artifacts = [
            _repo_relative_path(value, label=f"{task_id}.artifacts")
            for value in task["artifacts"]
        ]
        evidence_root = _repo_relative_path(
            task["evidence_root"],
            label=f"{task_id}.evidence_root",
        )
        if evidence_root not in artifacts:
            raise DispatchError(f"{task_id}.evidence_root must be a declared artifact")
        _repo_relative_path(task["task_doc"], label=f"{task_id}.task_doc")
        task["depends_on"] = dependencies
        task["artifacts"] = artifacts
        by_id[task_id] = task

    for task_id, task in by_id.items():
        for dependency in task["depends_on"]:
            if dependency in by_id:
                if wave_order[by_id[dependency]["wave"]] > wave_order[task["wave"]]:
                    raise DispatchError(
                        f"{task_id} depends on later-wave task {dependency}"
                    )
                continue
            observed_external_ids.add(dependency)
            if dependency not in profile["external_dependency_ids"]:
                raise DispatchError(f"{task_id} depends on unknown task {dependency}")
    if observed_external_ids != profile["external_dependency_ids"]:
        raise DispatchError("current catalog external dependency IDs are not exact")

    memo: dict[str, set[str]] = {}
    for task_id in by_id:
        _ancestors(task_id, by_id, memo)
    memo.pop("__visiting__", None)

    for index, left in enumerate(tasks):
        for right in tasks[index + 1 :]:
            overlap = any(
                artifact_overlaps(left_artifact, right_artifact)
                for left_artifact in left["artifacts"]
                for right_artifact in right["artifacts"]
            )
            if not overlap:
                continue
            left_id, right_id = str(left["id"]), str(right["id"])
            if left["wave"] == "G1" and right["wave"] == "G1":
                raise DispatchError(
                    "G1 artifact-prefix overlap is prohibited: "
                    f"{left_id} <-> {right_id}"
                )
            if left_id not in memo[right_id] and right_id not in memo[left_id]:
                raise DispatchError(
                    "overlapping artifact scopes require dependency order: "
                    f"{left_id} <-> {right_id}"
                )

    sinks = set(by_id)
    for task in tasks:
        sinks.difference_update(
            dependency for dependency in task["depends_on"] if dependency in by_id
        )
    verifier_id = "L12-VERIFY-LEARN-REAL-VERIFIER-001"
    if sinks != {verifier_id}:
        raise DispatchError("learning verifier rebuild must be the unique graph sink")
    if (memo[verifier_id] & set(by_id)) != set(by_id) - {verifier_id}:
        raise DispatchError("every current task must be an ancestor of the graph sink")

    wave_counts = Counter(task["wave"] for task in tasks)
    if wave_counts != Counter({"G1": 25, "G2": 2, "G3": 1}):
        raise DispatchError("current catalog wave counts are not exact")
    integration = by_id["L12-CONTROLLER-CATALOG-INTEGRATION-20260731"]
    release = by_id["L12-CURRENT-PROOF-RELEASE-GATE-20260731"]
    if integration["wave"] != "G2" or release["wave"] != "G2":
        raise DispatchError("current G2 topology is not exact")
    if integration["id"] not in release["depends_on"]:
        raise DispatchError("current release gate must follow shared integration")
    if release["id"] not in by_id[verifier_id]["depends_on"]:
        raise DispatchError("learning verifier must follow the current release gate")
    return [by_id[task_id] for task_id in task_ids]


def validate_catalog(
    catalog: dict[str, Any],
    *,
    repo_root: Path = REPO_ROOT,
) -> list[dict[str, Any]]:
    program_id = str(catalog.get("program_id") or "").strip()
    if program_id == PROGRAM_ID:
        return _validate_legacy_catalog(catalog, repo_root=repo_root)
    if program_id in CURRENT_PROFILE_BY_PROGRAM_ID:
        return _validate_current_catalog(catalog)
    raise DispatchError(f"unsupported twelve-loop catalog program_id: {program_id!r}")


def task_contract(
    task: dict[str, Any],
    *,
    catalog: dict[str, Any] | None = None,
) -> dict[str, Any]:
    is_current = (
        _is_current_catalog(catalog or {})
        or "owner_preference" in task
    )
    fields = CURRENT_REQUIRED_TASK_FIELDS if is_current else REQUIRED_TASK_FIELDS
    dynamic = CURRENT_DYNAMIC_TASK_FIELDS if is_current else DYNAMIC_TASK_FIELDS
    return {key: task[key] for key in sorted(fields - dynamic)}


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

    expected_contract = task_contract(task, catalog=catalog)
    archived_contract = {key: archived_task.get(key) for key in expected_contract}
    if archived_contract != expected_contract:
        raise DispatchError(f"archived task contract conflicts with catalog: {task_id}")
    expected_contract_sha256 = canonical_json_sha256(expected_contract)
    if (
        str(archived_task.get("catalog_task_contract_sha256") or "").strip()
        != expected_contract_sha256
    ):
        raise DispatchError(f"archived task contract digest conflicts with catalog: {task_id}")


def _plan_legacy_materialization(
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
        expected_contract = task_contract(task, catalog=catalog)
        active_contract = {key: active.get(key) for key in expected_contract}
        if active_contract != expected_contract:
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


def _validate_current_assignment_evaluations(
    evaluations: Any,
    *,
    preference: list[str],
    selected_agent: str,
    excluded_agents: set[str],
    label: str,
) -> list[dict[str, Any]]:
    if not isinstance(evaluations, list) or len(evaluations) != len(preference):
        raise DispatchError(f"{label} evaluations are not complete")
    for index, (evaluation, candidate) in enumerate(zip(evaluations, preference)):
        if (
            not isinstance(evaluation, dict)
            or set(evaluation) != CURRENT_ASSIGNMENT_EVALUATION_FIELDS
        ):
            raise DispatchError(f"{label} evaluation schema is not exact at {index}")
        if evaluation.get("agent") != candidate:
            raise DispatchError(f"{label} evaluation order conflicts at {index}")
        for field in ("ready", "selected", "considered"):
            if not isinstance(evaluation.get(field), bool):
                raise DispatchError(
                    f"{label} evaluation {field} is not boolean at {index}"
                )
        reasons = evaluation.get("reasons")
        if (
            not isinstance(reasons, list)
            or any(not isinstance(reason, str) or not reason.strip() for reason in reasons)
            or len(reasons) != len(set(reasons))
        ):
            raise DispatchError(f"{label} evaluation reasons are invalid at {index}")
        excluded = candidate in excluded_agents
        if excluded:
            if "same_as_owner" not in reasons or evaluation["ready"]:
                raise DispatchError(
                    f"{label} evaluation does not enforce owner exclusion at {index}"
                )
        elif "same_as_owner" in reasons:
            raise DispatchError(
                f"{label} evaluation claims an invalid owner exclusion at {index}"
            )
        if evaluation["ready"] != (not reasons):
            raise DispatchError(
                f"{label} evaluation readiness contradicts reasons at {index}"
            )

    ready_indexes = [
        index
        for index, evaluation in enumerate(evaluations)
        if evaluation["ready"]
    ]
    if not ready_indexes:
        raise DispatchError(f"{label} evaluations contain no selectable provider")
    selected_index = ready_indexes[0]
    for index, evaluation in enumerate(evaluations):
        if evaluation["considered"] != (index <= selected_index):
            raise DispatchError(
                f"{label} considered sequence conflicts with first-ready selection"
            )
        if evaluation["selected"] != (index == selected_index):
            raise DispatchError(
                f"{label} selected sequence conflicts with first-ready selection"
            )
    if preference[selected_index] != selected_agent:
        raise DispatchError(f"{label} selected provider conflicts with row assignment")
    return evaluations


def _validate_current_assignment_resolution(
    resolution: Any,
    *,
    owner: Any,
    reviewer: Any,
    catalog_owner: Any,
    catalog_reviewer: Any,
    owner_preference: list[str],
    reviewer_preference: list[str],
    label: str,
) -> None:
    if (
        not isinstance(resolution, dict)
        or set(resolution) != CURRENT_ASSIGNMENT_RESOLUTION_FIELDS
    ):
        raise DispatchError(f"{label} provider assignment resolution schema is not exact")
    if resolution.get("schema_version") != 1:
        raise DispatchError(f"{label} provider assignment schema version conflicts")
    if resolution.get("source") != "live-supervisor-readiness":
        raise DispatchError(f"{label} provider readiness source identity conflicts")
    _nonempty_string(resolution.get("observed_at"), label=f"{label} observed_at")
    readiness_sha256 = resolution.get("readiness_sha256")
    if (
        not isinstance(readiness_sha256, str)
        or len(readiness_sha256) != 64
        or any(character not in "0123456789abcdef" for character in readiness_sha256)
    ):
        raise DispatchError(f"{label} provider readiness digest is invalid")
    expected_identity = {
        "catalog_owner": catalog_owner,
        "catalog_reviewer": catalog_reviewer,
        "owner": owner,
        "reviewer": reviewer,
    }
    if any(resolution.get(key) != value for key, value in expected_identity.items()):
        raise DispatchError(f"{label} provider assignment identity conflicts")
    if owner == reviewer:
        raise DispatchError(f"{label} provider assignment collides")

    owner_evaluations = _validate_current_assignment_evaluations(
        resolution["owner_evaluations"],
        preference=owner_preference,
        selected_agent=str(owner),
        excluded_agents=set(),
        label=f"{label} owner",
    )
    reviewer_evaluations = _validate_current_assignment_evaluations(
        resolution["reviewer_evaluations"],
        preference=reviewer_preference,
        selected_agent=str(reviewer),
        excluded_agents={str(owner)},
        label=f"{label} reviewer",
    )
    expected_owner_fallbacks = [
        evaluation
        for evaluation in owner_evaluations
        if evaluation["considered"]
        and not evaluation["selected"]
        and evaluation["reasons"]
    ]
    expected_reviewer_fallbacks = [
        evaluation
        for evaluation in reviewer_evaluations
        if evaluation["considered"]
        and not evaluation["selected"]
        and evaluation["reasons"]
    ]
    if resolution.get("owner_fallbacks") != expected_owner_fallbacks:
        raise DispatchError(f"{label} owner fallback evidence conflicts")
    if resolution.get("reviewer_fallbacks") != expected_reviewer_fallbacks:
        raise DispatchError(f"{label} reviewer fallback evidence conflicts")


def _current_materialized_row_is_exact(
    row: dict[str, Any],
    *,
    catalog: dict[str, Any],
    task: dict[str, Any],
) -> None:
    profile = _current_profile(catalog)
    if profile is None:
        raise DispatchError("current task catalog profile is missing")
    task_id = str(task["id"])
    expected_contract = task_contract(task, catalog=catalog)
    actual_contract = {key: row.get(key) for key in expected_contract}
    if actual_contract != expected_contract:
        raise DispatchError(f"current task contract conflicts with catalog: {task_id}")
    if row.get("program_id") != catalog["program_id"]:
        raise DispatchError(f"current task program identity conflicts: {task_id}")
    if row.get("auto_created_by") != AUTO_CREATED_BY:
        raise DispatchError(f"current task creator identity conflicts: {task_id}")
    if row.get("catalog_task_contract_sha256") != canonical_json_sha256(
        expected_contract
    ):
        raise DispatchError(f"current task contract digest conflicts: {task_id}")
    source = row.get("catalog_source")
    if not isinstance(source, dict) or source != {
        "catalog_file_sha256": profile["catalog_file_sha256"],
        "source_pr": profile["source_pr"],
        "source_head": profile["source_head"],
        "source_branch_ci_run": profile["source_branch_ci_run"],
    }:
        raise DispatchError(f"current task source binding conflicts: {task_id}")
    catalog_defaults = row.get("catalog_assignment_defaults")
    if not isinstance(catalog_defaults, dict) or catalog_defaults != {
        "owner": task["owner"],
        "reviewer": task["reviewer"],
    }:
        raise DispatchError(f"current task catalog assignment defaults conflict: {task_id}")
    _validate_current_assignment_resolution(
        row.get("provider_assignment_resolution"),
        owner=row.get("owner"),
        reviewer=row.get("reviewer"),
        catalog_owner=task["owner"],
        catalog_reviewer=task["reviewer"],
        owner_preference=task["owner_preference"],
        reviewer_preference=task["reviewer_preference"],
        label=f"current task {task_id}",
    )


def _terminal_dependency_truth(
    task_id: str,
    *,
    active_by_id: dict[str, dict[str, Any]],
    status_root: Path,
) -> dict[str, Any]:
    active = active_by_id.get(task_id)
    archive_path = next(
        (path for path in _archive_candidates(status_root, task_id) if path.is_file()),
        None,
    )
    if active is not None and archive_path is not None:
        raise DispatchError(
            f"dependency is present in both active and archive truth: {task_id}"
        )
    if active is not None:
        status = str(active.get("status") or "").strip()
        return {"source": "active", "status": status, "satisfied": status == "done"}
    if archive_path is not None:
        archive = load_json_object(archive_path)
        status = str(archive.get("terminal_status") or "").strip()
        return {"source": "archive", "status": status, "satisfied": status == "done"}
    raise DispatchError(
        f"dependency is absent from active and archive truth: {task_id}"
    )


def _current_task_materialization_truth(
    *,
    catalog: dict[str, Any],
    tasks: list[dict[str, Any]],
    active_by_id: dict[str, dict[str, Any]],
    status_root: Path,
) -> dict[str, dict[str, Any]]:
    truth: dict[str, dict[str, Any]] = {}
    for task in tasks:
        task_id = str(task["id"])
        active = active_by_id.get(task_id)
        archive_path = next(
            (path for path in _archive_candidates(status_root, task_id) if path.is_file()),
            None,
        )
        if active is not None and archive_path is not None:
            raise DispatchError(
                f"task ID is present in both active and archive truth: {task_id}"
            )
        if archive_path is not None:
            _validate_exact_archived_task(
                archive_path,
                catalog=catalog,
                task=task,
            )
            archive = load_json_object(archive_path)
            row = archive.get("task")
            if not isinstance(row, dict):
                raise DispatchError(f"archived task record is malformed: {task_id}")
            _current_materialized_row_is_exact(row, catalog=catalog, task=task)
            truth[task_id] = {
                "source": "archive",
                "status": "done",
                "satisfied": True,
                "row": row,
            }
            continue
        if active is not None:
            _current_materialized_row_is_exact(active, catalog=catalog, task=task)
            status = str(active.get("status") or "").strip()
            truth[task_id] = {
                "source": "active",
                "status": status,
                "satisfied": status == "done",
                "row": active,
            }
            continue
        truth[task_id] = {
            "source": "missing",
            "status": "missing",
            "satisfied": False,
            "row": None,
        }
    return truth


def _held_close_overlap_is_release_ordered(
    *,
    catalog: dict[str, Any],
    tasks: list[dict[str, Any]],
    incoming: dict[str, Any],
    incoming_scope: list[tuple[str, str]],
    active_close: dict[str, Any],
) -> bool:
    """Admit only the pinned previous-current integration-to-close edge."""

    if (
        catalog.get("program_id") != PREVIOUS_CURRENT_PROGRAM_ID
        or str(incoming.get("id") or "")
        != "L12-CONTROLLER-CATALOG-INTEGRATION-20260731"
    ):
        return False
    active_scope = task_artifact_scope(active_close)
    overlapping_pairs = {
        (left_repo, left_path, right_repo, right_path)
        for left_repo, left_path in incoming_scope
        for right_repo, right_path in active_scope
        if left_repo == right_repo and artifact_overlaps(left_path, right_path)
    }
    if overlapping_pairs != {
        (
            "pantheon",
            HELD_CLOSE_REGISTRY_ARTIFACT,
            "pantheon",
            HELD_CLOSE_REGISTRY_ARTIFACT,
        )
    }:
        return False

    owner = str(active_close.get("owner") or "").strip()
    reviewer = str(active_close.get("reviewer") or "").strip()
    if (
        str(active_close.get("status") or "").strip() != "todo"
        or owner not in CURRENT_ALLOWED_FLEET_ACTORS
        or reviewer not in CURRENT_ALLOWED_FLEET_ACTORS
        or owner == reviewer
        or active_close.get("depends_on") != HELD_CLOSE_DEPENDENCIES
        or active_close.get("artifacts") != HELD_CLOSE_ARTIFACTS
        or active_close.get("program_id") != PROGRAM_ID
        or active_close.get("auto_created_by") != AUTO_CREATED_BY
        or active_close.get("catalog_task_contract_sha256")
        != HELD_CLOSE_CATALOG_TASK_CONTRACT_SHA256
        or active_close.get("artifact_conflict_guard")
        != HELD_CLOSE_ARTIFACT_CONFLICT_GUARD
        or active_close.get("target_repo") != "pantheon"
        or active_close.get("merge_target") != "dev"
        or active_close.get("evidence_root")
        != "docs/deployment/evidence/twelve-loop-gap/L12-CLOSE-001"
        or active_close.get("requires_human_ops_signoff") is not True
    ):
        return False

    # This binds the full previous-current graph, including the release gate
    # that holds close behind controller integration and hosted proof. The
    # per-task digests also reject callers that mutate the validated task list.
    if canonical_json_sha256(catalog) != PREVIOUS_CURRENT_CATALOG_CANONICAL_SHA256:
        return False
    by_id = {str(task.get("id") or ""): task for task in tasks}
    for (
        task_id,
        expected_sha256,
    ) in PREVIOUS_HELD_CLOSE_RELEASE_ORDER_CONTRACT_SHA256.items():
        task = by_id.get(task_id)
        if task is None or canonical_json_sha256(
            task_contract(task, catalog=catalog)
        ) != expected_sha256:
            return False
    return True


def _current_live_overlap_guard(
    *,
    catalog: dict[str, Any],
    tasks: list[dict[str, Any]],
    active_by_id: dict[str, dict[str, Any]],
) -> None:
    profile = _current_profile(catalog)
    if profile is None:
        raise DispatchError("current live-overlap catalog profile is missing")
    catalog_ids = {str(task["id"]) for task in tasks}
    external_ids = profile["external_dependency_ids"] | CURRENT_RUNTIME_GATE_IDS
    for task in tasks:
        catalog_scope = task_artifact_scope(task)
        for active_id, active in active_by_id.items():
            if active_id == task["id"] or str(active.get("status") or "") in {
                "done",
                "cancelled",
                "canceled",
                "supersede",
                "superseded",
            }:
                continue
            held_close_pair = (
                catalog.get("program_id") == PREVIOUS_CURRENT_PROGRAM_ID
                and active_id == HELD_CLOSE_TASK_ID
                and task["id"] == "L12-CONTROLLER-CATALOG-INTEGRATION-20260731"
            )
            overlap = any(
                left_repo == right_repo and artifact_overlaps(left_path, right_path)
                for left_repo, left_path in catalog_scope
                for right_repo, right_path in task_artifact_scope(active)
            )
            # A malformed held-close row must not hide the registry collision
            # by dropping its artifacts or spoofing its target repository.
            if not overlap and not held_close_pair:
                continue
            if active_id in catalog_ids:
                continue
            if active_id in external_ids and active_id in task["depends_on"]:
                continue
            if held_close_pair and _held_close_overlap_is_release_ordered(
                catalog=catalog,
                tasks=tasks,
                incoming=task,
                incoming_scope=catalog_scope,
                active_close=active,
            ):
                continue
            raise DispatchError(
                "live nonterminal artifact overlap is not dependency-ordered: "
                f"{task['id']} <-> {active_id}"
            )


def _readiness_candidate(
    candidate: str,
    *,
    config: dict[str, Any],
    runtime_state: dict[str, Any],
    provider_capabilities: dict[str, Any],
) -> dict[str, Any]:
    agent_id = candidate.strip().lower()
    agents = config.get("agents") if isinstance(config.get("agents"), dict) else {}
    agent = agents.get(agent_id) if isinstance(agents, dict) else None
    reasons: list[str] = []
    if not isinstance(agent, dict):
        reasons.append("unknown_agent")
        agent = {}
    ready_dispatcher = (
        config.get("ready_dispatcher")
        if isinstance(config.get("ready_dispatcher"), dict)
        else {}
    )
    disabled = {
        str(value).strip().casefold()
        for value in ready_dispatcher.get("disabled_agents", [])
    }
    sidecar = {
        str(value).strip().casefold()
        for value in ready_dispatcher.get("sidecar_only_agents", [])
    }
    if candidate.casefold() in disabled or agent_id.casefold() in disabled:
        reasons.append("dispatch_disabled")
    if candidate.casefold() in sidecar or agent_id.casefold() in sidecar:
        reasons.append("sidecar_only")

    provider_id = str(agent.get("provider") or agent_id).strip()
    providers = config.get("providers") if isinstance(config.get("providers"), dict) else {}
    provider_config = providers.get(provider_id) if isinstance(providers, dict) else {}
    if not isinstance(provider_config, dict):
        provider_config = {}
    account = str(provider_config.get("account") or "").strip()
    pause_bucket = (
        ((runtime_state.get("provider_guardrails") or {}).get("dispatch_pauses") or {})
        if isinstance(runtime_state.get("provider_guardrails"), dict)
        else {}
    )
    pause_ids = {
        value.casefold()
        for value in (candidate, agent_id, provider_id, account)
        if value
    }
    if any(str(key).casefold() in pause_ids for key in pause_bucket):
        reasons.append("dispatch_paused")

    provider_rows = (
        provider_capabilities.get("providers")
        if isinstance(provider_capabilities.get("providers"), dict)
        else {}
    )
    capability = provider_rows.get(provider_id) if isinstance(provider_rows, dict) else None
    if not isinstance(capability, dict):
        reasons.append("provider_capability_missing")
        capability = {}
    else:
        if capability.get("auth_ready") is not True:
            reasons.append("auth_not_ready")
        if capability.get("local_cli_worker_supported") is not True:
            reasons.append("local_worker_not_ready")
        if capability.get("supports_auto_approve") is not True:
            reasons.append("auto_approve_not_ready")

    adapter_rows = (
        provider_capabilities.get("agent_adapters")
        if isinstance(provider_capabilities.get("agent_adapters"), dict)
        else {}
    )
    adapter = adapter_rows.get(agent_id) if isinstance(adapter_rows, dict) else None
    if not isinstance(adapter, dict):
        reasons.append("agent_adapter_missing")
    else:
        if adapter.get("supported") is not True:
            reasons.append("adapter_not_supported")
        if adapter.get("can_auto_deliver") is not True:
            reasons.append("auto_delivery_not_ready")

    return {
        "agent": candidate,
        "agent_id": agent_id,
        "provider": provider_id,
        "account": account or None,
        "ready": not reasons,
        "reasons": reasons,
        "auth_probe_status": (
            ((capability.get("auth_probe") or {}).get("status"))
            if isinstance(capability.get("auth_probe"), dict)
            else None
        ),
        "last_auth_probe_at": capability.get("last_auth_probe_at"),
    }


def build_current_readiness_snapshot(
    *,
    config: dict[str, Any],
    runtime_state: dict[str, Any],
    provider_capabilities: dict[str, Any],
) -> dict[str, Any]:
    candidates = {
        candidate: _readiness_candidate(
            candidate,
            config=config,
            runtime_state=runtime_state,
            provider_capabilities=provider_capabilities,
        )
        for candidate in CURRENT_OWNER_PREFERENCE
    }
    snapshot = {
        "schema_version": 1,
        "source": "live-supervisor-readiness",
        "observed_at": (
            (runtime_state.get("supervisor") or {}).get("last_successful_loop_at")
            if isinstance(runtime_state.get("supervisor"), dict)
            else None
        ),
        "provider_capabilities_generated_at": provider_capabilities.get("generated_at"),
        "candidates": candidates,
    }
    snapshot["sha256"] = canonical_json_sha256(snapshot)
    return snapshot


def load_current_readiness_snapshot(
    *,
    config_path: Path,
    runtime_state_path: Path,
    provider_capabilities_path: Path,
) -> dict[str, Any]:
    return build_current_readiness_snapshot(
        config=load_json_object(config_path),
        runtime_state=load_json_object(runtime_state_path),
        provider_capabilities=load_json_object(provider_capabilities_path),
    )


def resolve_current_assignment(
    task: dict[str, Any],
    *,
    readiness: dict[str, Any],
) -> dict[str, Any]:
    candidates = readiness.get("candidates")
    if not isinstance(candidates, dict):
        raise DispatchError("provider readiness candidates must be an object")

    def select(preference: list[str], *, exclude: set[str]) -> tuple[str, list[dict[str, Any]]]:
        evaluations: list[dict[str, Any]] = []
        selected: str | None = None
        for candidate in preference:
            considered = selected is None
            raw = candidates.get(candidate)
            if not isinstance(raw, dict):
                evaluation = {
                    "agent": candidate,
                    "ready": False,
                    "reasons": ["readiness_missing"],
                    "selected": False,
                    "considered": considered,
                }
            else:
                reasons = list(raw.get("reasons") or [])
                if candidate in exclude:
                    reasons.append("same_as_owner")
                ready = raw.get("ready") is True and not reasons
                evaluation = {
                    "agent": candidate,
                    "ready": ready,
                    "reasons": reasons,
                    "selected": False,
                    "considered": considered,
                }
            if considered and evaluation["ready"]:
                selected = candidate
                evaluation["selected"] = True
            evaluations.append(evaluation)
        if selected is None:
            raise DispatchError(
                "no live-ready provider satisfies assignment preference: "
                + ", ".join(preference)
            )
        return selected, evaluations

    owner, owner_evaluations = select(task["owner_preference"], exclude=set())
    reviewer, reviewer_evaluations = select(
        task["reviewer_preference"],
        exclude={owner},
    )
    resolved = deepcopy(task)
    resolved["owner"] = owner
    resolved["reviewer"] = reviewer
    resolved["provider_assignment_resolution"] = {
        "schema_version": 1,
        "source": readiness.get("source"),
        "readiness_sha256": readiness.get("sha256"),
        "observed_at": readiness.get("observed_at"),
        "catalog_owner": task["owner"],
        "catalog_reviewer": task["reviewer"],
        "owner": owner,
        "reviewer": reviewer,
        "owner_evaluations": owner_evaluations,
        "reviewer_evaluations": reviewer_evaluations,
        "owner_fallbacks": [
            item
            for item in owner_evaluations
            if item["considered"] and not item["selected"] and item["reasons"]
        ],
        "reviewer_fallbacks": [
            item
            for item in reviewer_evaluations
            if item["considered"] and not item["selected"] and item["reasons"]
        ],
    }
    return resolved


def _plan_current_materialization(
    catalog: dict[str, Any],
    tasks: list[dict[str, Any]],
    *,
    status_root: Path,
    state: dict[str, Any],
    readiness: dict[str, Any] | None,
) -> dict[str, Any]:
    profile = _current_profile(catalog)
    if profile is None:
        raise DispatchError("current materialization catalog profile is missing")
    active_tasks = state.get("tasks")
    if not isinstance(active_tasks, list):
        raise DispatchError("authoritative task-state tasks must be a list")
    active_by_id: dict[str, dict[str, Any]] = {}
    for item in active_tasks:
        if not isinstance(item, dict):
            raise DispatchError("authoritative task entries must be objects")
        task_id = str(item.get("id") or "").strip()
        if not task_id:
            continue
        if task_id in active_by_id:
            raise DispatchError(f"duplicate active task ID: {task_id}")
        active_by_id[task_id] = item

    dependency_ids = profile["external_dependency_ids"] | CURRENT_RUNTIME_GATE_IDS
    dependency_truth = {
        task_id: _terminal_dependency_truth(
            task_id,
            active_by_id=active_by_id,
            status_root=status_root,
        )
        for task_id in sorted(dependency_ids)
    }
    _current_live_overlap_guard(
        catalog=catalog,
        tasks=tasks,
        active_by_id=active_by_id,
    )
    materialized = _current_task_materialization_truth(
        catalog=catalog,
        tasks=tasks,
        active_by_id=active_by_id,
        status_root=status_root,
    )
    g1_ids = {task["id"] for task in tasks if task["wave"] == "G1"}
    g1_materialized = {
        task_id for task_id in g1_ids if materialized[task_id]["source"] != "missing"
    }
    if g1_materialized and g1_materialized != g1_ids:
        raise DispatchError(
            "partial materialization detected for immutable G1 frontier: "
            f"present={len(g1_materialized)} expected={len(g1_ids)}"
        )

    create: list[dict[str, Any]] = []
    deferred: list[dict[str, Any]] = []
    exact = [
        task["id"]
        for task in tasks
        if materialized[task["id"]]["source"] != "missing"
    ]
    for task in tasks:
        task_id = str(task["id"])
        if materialized[task_id]["source"] != "missing":
            continue
        dependencies_satisfied = True
        for dependency in task["depends_on"]:
            if dependency in materialized:
                if not materialized[dependency]["satisfied"]:
                    dependencies_satisfied = False
                    break
            elif not dependency_truth[dependency]["satisfied"]:
                dependencies_satisfied = False
                break
        if any(
            not dependency_truth[gate_id]["satisfied"]
            for gate_id in CURRENT_RUNTIME_GATE_IDS
        ):
            dependencies_satisfied = False
        if dependencies_satisfied:
            if readiness is None:
                raise DispatchError("live provider readiness is required for current task admission")
            create.append(resolve_current_assignment(task, readiness=readiness))
        else:
            deferred.append(task)

    assignment_decisions = {
        task_id: materialized[task_id]["row"]["provider_assignment_resolution"]
        for task_id in exact
    }
    assignment_decisions.update(
        {
            task["id"]: task["provider_assignment_resolution"] for task in create
        }
    )
    return {
        "program_id": catalog["program_id"],
        "catalog_sha256": canonical_json_sha256(catalog),
        "catalog_file_sha256": profile["catalog_file_sha256"],
        "source_specification": {
            "pull_request": profile["source_pr"],
            "head_sha": profile["source_head"],
            "branch_ci_run": profile["source_branch_ci_run"],
            "branch_ci_conclusion": "success",
        },
        "status_root": str(status_root),
        "external_dependencies": dependency_truth,
        "readiness_sha256": readiness.get("sha256") if readiness else None,
        "create": [task["id"] for task in create],
        "exact": exact,
        "deferred": [task["id"] for task in deferred],
        "create_tasks": create,
        "assignment_decisions": assignment_decisions,
    }


def plan_materialization(
    catalog: dict[str, Any],
    tasks: list[dict[str, Any]],
    *,
    status_root: Path,
    state: dict[str, Any],
    readiness: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if _is_current_catalog(catalog):
        return _plan_current_materialization(
            catalog,
            tasks,
            status_root=status_root,
            state=state,
            readiness=readiness,
        )
    return _plan_legacy_materialization(
        catalog,
        tasks,
        status_root=status_root,
        state=state,
    )


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
    external_ids = (
        set(_current_profile(catalog)["external_dependency_ids"])
        if _is_current_catalog(catalog)
        else {dependency["id"] for dependency in EXPECTED_EXTERNAL_DEPENDENCIES}
    )
    allowed.update(external_ids & set(task["depends_on"]))
    return {
        "schema_version": 1,
        "program_id": catalog["program_id"],
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
            "program_id": catalog["program_id"],
            "catalog_task_contract_sha256": canonical_json_sha256(
                task_contract(task, catalog=catalog)
            ),
            "artifact_conflict_guard": artifact_conflict_guard(
                task,
                catalog=catalog,
                tasks=tasks,
            ),
            "auto_created_by": AUTO_CREATED_BY,
            "mutates_canonical": True,
        }
    )
    profile = _current_profile(catalog)
    if profile is not None:
        metadata.update(
            {
                "catalog_source": {
                    "catalog_file_sha256": profile["catalog_file_sha256"],
                    "source_pr": profile["source_pr"],
                    "source_head": profile["source_head"],
                    "source_branch_ci_run": profile["source_branch_ci_run"],
                },
                "catalog_assignment_defaults": {
                    "owner": next(
                        item["owner"]
                        for item in catalog["tasks"]
                        if item["id"] == task["id"]
                    ),
                    "reviewer": next(
                        item["reviewer"]
                        for item in catalog["tasks"]
                        if item["id"] == task["id"]
                    ),
                },
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


@contextmanager
def temporary_environment(values: dict[str, str]):
    previous = {key: os.environ.get(key) for key in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _load_ai_status_module(script: Path):
    module_name = "pantheon_guarded_dispatch_ai_status"
    spec = importlib.util.spec_from_file_location(module_name, script)
    if spec is None or spec.loader is None:
        raise DispatchError(f"cannot load canonical status writer: {script}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def materialize_current_in_memory(
    state: dict[str, Any],
    plan: dict[str, Any],
    *,
    catalog: dict[str, Any],
    tasks: list[dict[str, Any]],
    assign_one: Callable[[dict[str, Any], dict[str, Any], dict[str, str]], None],
) -> dict[str, Any]:
    """Return a fully admitted copy; the input is unchanged on any failure."""

    working = deepcopy(state)
    for task in plan["create_tasks"]:
        env = assignment_environment(task, catalog=catalog, tasks=tasks)
        assign_one(working, task, env)
    return working


def verify_current_canonical_readback(
    *,
    catalog: dict[str, Any],
    tasks: list[dict[str, Any]],
    status_root: Path,
    authority: dict[str, Any],
    admitted_task_ids: list[str],
    readiness: dict[str, Any],
) -> dict[str, Any]:
    authoritative_snapshot = load_authoritative_task_snapshot(authority)
    authoritative = authoritative_snapshot["state"]
    projection = load_json_object(status_root / "ai-status.json")
    if canonical_json_sha256(authoritative) != canonical_json_sha256(projection):
        raise DispatchError("canonical ai-status/task-state readback mismatch")
    readback_plan = plan_materialization(
        catalog,
        tasks,
        status_root=status_root,
        state=authoritative,
        readiness=readiness,
    )
    not_exact = sorted(set(admitted_task_ids) - set(readback_plan["exact"]))
    if not_exact:
        raise DispatchError(
            "canonical readback is missing admitted tasks: " + ", ".join(not_exact)
        )
    rows = {
        str(task.get("id")): task
        for task in authoritative.get("tasks", [])
        if isinstance(task, dict)
    }
    expected = {task["id"]: task for task in tasks}
    for task_id in admitted_task_ids:
        row = rows.get(task_id)
        if row is None:
            archive_path = next(
                (
                    path
                    for path in _archive_candidates(status_root, task_id)
                    if path.is_file()
                ),
                None,
            )
            if archive_path is None:
                raise DispatchError(f"exact readback row is absent: {task_id}")
            archive = load_json_object(archive_path)
            row = archive.get("task")
        if not isinstance(row, dict):
            raise DispatchError(f"exact readback row is malformed: {task_id}")
        _current_materialized_row_is_exact(
            row,
            catalog=catalog,
            task=expected[task_id],
        )
    return {
        "state_sha256": canonical_json_sha256(authoritative),
        "projection_sha256": canonical_json_sha256(projection),
        "exact": admitted_task_ids,
        "task_state_snapshot": authoritative_snapshot_evidence(
            authoritative_snapshot
        ),
    }


def _admission_archive_path(
    status_root: Path,
    *,
    program_id: str,
    catalog_sha256: str,
    admitted_task_ids: list[str],
) -> Path:
    admission_id = canonical_json_sha256(
        {
            "program_id": program_id,
            "catalog_sha256": catalog_sha256,
            "admitted_task_ids": admitted_task_ids,
        }
    )
    return (
        status_root
        / ".orchestrator"
        / "program-dispatch-admissions"
        / program_id
        / f"{admission_id}.json"
    )


def _write_current_admission_archive_payload(
    path: Path,
    payload: dict[str, Any],
) -> None:
    serialized = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    durable_write_bytes(path, serialized.encode("utf-8"), mode=0o600)
    if path.read_text(encoding="utf-8") != serialized:
        raise DispatchError("admission archive readback mismatch")


def _validate_current_admission_archive(
    payload: dict[str, Any],
    *,
    path: Path,
    plan: dict[str, Any],
    admitted_task_ids: list[str],
    command_runtime: dict[str, str],
) -> None:
    required = {
        "schema_version",
        "admission_id",
        "program_id",
        "catalog_sha256",
        "catalog_file_sha256",
        "source_specification",
        "prepared_by",
        "finalized_by",
        "command_runtime",
        "admitted_task_ids",
        "assignment_decisions",
        "status",
        "prepared_at",
        "committed_at",
        "canonical_readback",
    }
    if set(payload) != required:
        raise DispatchError(f"admission archive schema is not exact: {path}")
    assignment_decisions = payload.get("assignment_decisions")
    if not isinstance(assignment_decisions, dict) or set(assignment_decisions) != set(
        admitted_task_ids
    ):
        raise DispatchError(f"admission archive assignment decisions are not exact: {path}")
    plan_decisions = plan.get("assignment_decisions")
    if not isinstance(plan_decisions, dict):
        raise DispatchError(f"admission plan assignment decisions are malformed: {path}")
    for task_id in admitted_task_ids:
        expected_decision = plan_decisions.get(task_id)
        if not isinstance(expected_decision, dict):
            raise DispatchError(
                f"admission plan lacks assignment decision for {task_id}: {path}"
            )
        _validate_current_assignment_resolution(
            assignment_decisions[task_id],
            owner=expected_decision.get("owner"),
            reviewer=expected_decision.get("reviewer"),
            catalog_owner=expected_decision.get("catalog_owner"),
            catalog_reviewer=expected_decision.get("catalog_reviewer"),
            owner_preference=CURRENT_OWNER_PREFERENCE,
            reviewer_preference=CURRENT_REVIEWER_PREFERENCE,
            label=f"admission archive task {task_id}",
        )
    expected = {
        "schema_version": 1,
        "admission_id": path.stem,
        "program_id": plan["program_id"],
        "catalog_sha256": plan["catalog_sha256"],
        "catalog_file_sha256": plan["catalog_file_sha256"],
        "source_specification": plan["source_specification"],
        "command_runtime": {
            "source_sha": command_runtime["source_sha"],
            "remote": command_runtime["remote"],
            "base_ref": command_runtime["base_ref"],
        },
        "admitted_task_ids": admitted_task_ids,
        "assignment_decisions": {
            task_id: plan["assignment_decisions"][task_id]
            for task_id in admitted_task_ids
        },
    }
    for key, value in expected.items():
        if payload.get(key) != value:
            raise DispatchError(f"admission archive {key} conflicts: {path}")
    if payload.get("status") not in {"prepared", "committed"}:
        raise DispatchError(f"admission archive status is invalid: {path}")
    if not str(payload.get("prepared_by") or "").strip() or not str(
        payload.get("prepared_at") or ""
    ).strip():
        raise DispatchError(f"admission archive preparation is incomplete: {path}")
    if payload["status"] == "prepared":
        if any(
            payload.get(key) is not None
            for key in ("finalized_by", "committed_at", "canonical_readback")
        ):
            raise DispatchError(f"prepared admission archive claims commit: {path}")
        return
    readback = payload.get("canonical_readback")
    if (
        not str(payload.get("finalized_by") or "").strip()
        or not str(payload.get("committed_at") or "").strip()
        or not isinstance(readback, dict)
        or readback.get("exact") != admitted_task_ids
        or readback.get("state_sha256") != readback.get("projection_sha256")
    ):
        raise DispatchError(f"committed admission archive proof is incomplete: {path}")


def prepare_current_admission_archive(
    *,
    status_root: Path,
    plan: dict[str, Any],
    admitted_task_ids: list[str],
    command_runtime: dict[str, str],
    actor: str,
    allow_committed: bool,
) -> Path:
    """Durably record admission intent before canonical task-state commit."""

    path = _admission_archive_path(
        status_root,
        program_id=str(plan["program_id"]),
        catalog_sha256=plan["catalog_sha256"],
        admitted_task_ids=admitted_task_ids,
    )
    symlink = _first_symlink_component(path.parent)
    if symlink is not None:
        raise DispatchError(f"admission archive parent contains symlink: {symlink}")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise DispatchError(f"admission archive cannot be a symlink: {path}")
    payload: dict[str, Any] = {
        "schema_version": 1,
        "admission_id": path.stem,
        "program_id": plan["program_id"],
        "catalog_sha256": plan["catalog_sha256"],
        "catalog_file_sha256": plan["catalog_file_sha256"],
        "source_specification": plan["source_specification"],
        "prepared_by": actor,
        "finalized_by": None,
        "command_runtime": {
            "source_sha": command_runtime["source_sha"],
            "remote": command_runtime["remote"],
            "base_ref": command_runtime["base_ref"],
        },
        "admitted_task_ids": admitted_task_ids,
        "assignment_decisions": {
            task_id: plan["assignment_decisions"][task_id]
            for task_id in admitted_task_ids
        },
        "status": "prepared",
        "prepared_at": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "committed_at": None,
        "canonical_readback": None,
    }
    if path.exists():
        existing = load_json_object(path)
        _validate_current_admission_archive(
            existing,
            path=path,
            plan=plan,
            admitted_task_ids=admitted_task_ids,
            command_runtime=command_runtime,
        )
        if existing["status"] == "committed" and not allow_committed:
            raise DispatchError(
                "committed admission archive conflicts with absent canonical tasks"
            )
        return path
    _write_current_admission_archive_payload(path, payload)
    return path


def finalize_current_admission_archive(
    *,
    path: Path,
    plan: dict[str, Any],
    admitted_task_ids: list[str],
    command_runtime: dict[str, str],
    actor: str,
    readback: dict[str, Any],
) -> Path:
    """Promote a prepared intent after exact canonical journal/projection readback."""

    existing = load_json_object(path)
    _validate_current_admission_archive(
        existing,
        path=path,
        plan=plan,
        admitted_task_ids=admitted_task_ids,
        command_runtime=command_runtime,
    )
    if existing["status"] == "committed":
        return path
    finalized = deepcopy(existing)
    finalized.update(
        {
            "status": "committed",
            "finalized_by": actor,
            "committed_at": datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z"),
            "canonical_readback": readback,
        }
    )
    _validate_current_admission_archive(
        finalized,
        path=path,
        plan=plan,
        admitted_task_ids=admitted_task_ids,
        command_runtime=command_runtime,
    )
    _write_current_admission_archive_payload(path, finalized)
    return path


def apply_current_materialization_atomic(
    *,
    catalog: dict[str, Any],
    tasks: list[dict[str, Any]],
    status_root: Path,
    authority: dict[str, Any],
    command_runtime: dict[str, str],
    config_path: Path,
    runtime_state_path: Path,
    provider_capabilities_path: Path,
) -> dict[str, Any]:
    actor = os.environ.get("AI_NAME", "").strip()
    if actor not in CURRENT_ALLOWED_FLEET_ACTORS:
        raise DispatchError(
            "--apply requires the real governed caller identity; "
            f"AI_NAME={actor!r} is not allowed"
        )
    profile = _current_profile(catalog)
    if profile is None:
        raise DispatchError("--apply catalog profile is not current-proof")
    command_root = Path(command_runtime["root"])
    base_env = {
        "PANTHEON_STATUS_ROOT": str(status_root),
        "PANTHEON_TASK_STATE_STORE_MODE": str(authority["mode"]),
        "PANTHEON_TASK_STATE_EVENT_LOG": str(authority["event_log"]),
        "PANTHEON_COMMAND_ROOT": str(command_root),
        "PANTHEON_COMMAND_RUNTIME_SHA": command_runtime["source_sha"],
        "PANTHEON_COMMAND_REMOTE": command_runtime["remote"],
        "PANTHEON_COMMAND_BASE_REF": command_runtime["base_ref"],
        "AI_NAME": actor,
    }
    with temporary_environment(base_env):
        ai_status = _load_ai_status_module(Path(command_runtime["script"]))
        ai_status.validate_status_command_runtime_binding()
        ai_status.validate_status_root_binding()
        config = ai_status.load_config()
        committed_state: dict[str, Any] | None = None
        final_plan: dict[str, Any] | None = None
        readiness: dict[str, Any] | None = None
        prepared_archive_path: Path | None = None
        archive_ids: list[str] = []
        with ai_status.runtime_state_lock(config, shared=True):
            readiness = load_current_readiness_snapshot(
                config_path=config_path,
                runtime_state_path=runtime_state_path,
                provider_capabilities_path=provider_capabilities_path,
            )
            with ai_status.canonical_task_state_lock(shared=False):
                with ai_status.authoritative_task_state_transaction():
                    state = ai_status.load_state()
                    ai_status.recover_status_archive_outbox(state)
                    ai_status.recover_status_activity_outbox(state)
                    final_plan = plan_materialization(
                        catalog,
                        tasks,
                        status_root=status_root,
                        state=state,
                        readiness=readiness,
                    )
                    archive_ids = list(final_plan["create"]) or [
                        task_id
                        for task_id in final_plan["exact"]
                        if task_id
                        in {task["id"] for task in tasks if task["wave"] == "G1"}
                    ]
                    if not final_plan["create_tasks"]:
                        if archive_ids:
                            prepared_archive_path = prepare_current_admission_archive(
                                status_root=status_root,
                                plan=final_plan,
                                admitted_task_ids=archive_ids,
                                command_runtime=command_runtime,
                                actor=actor,
                                allow_committed=True,
                            )
                        committed_state = deepcopy(state)
                    else:
                        with ai_status.buffer_activity_events():
                            def assign_one(
                                working: dict[str, Any],
                                task: dict[str, Any],
                                task_env: dict[str, str],
                            ) -> None:
                                env = {
                                    **task_env,
                                    "TASK_ASSIGN_CREATE_ONLY": "true",
                                }
                                with temporary_environment(env):
                                    ai_status.command_assign(
                                        working,
                                        [
                                            task["id"],
                                            task["owner"],
                                            task["reviewer"],
                                            task["title"],
                                        ],
                                    )

                            working = materialize_current_in_memory(
                                state,
                                final_plan,
                                catalog=catalog,
                                tasks=tasks,
                                assign_one=assign_one,
                            )
                            post_plan = plan_materialization(
                                catalog,
                                tasks,
                                status_root=status_root,
                                state=working,
                                readiness=readiness,
                            )
                            if set(final_plan["create"]) - set(post_plan["exact"]):
                                raise DispatchError(
                                    "in-memory exact readback failed before atomic commit"
                                )
                            prepared_archive_path = prepare_current_admission_archive(
                                status_root=status_root,
                                plan=final_plan,
                                admitted_task_ids=archive_ids,
                                command_runtime=command_runtime,
                                actor=actor,
                                allow_committed=False,
                            )
                            ai_status.append_log(
                                {
                                    "ts": ai_status.iso_now(),
                                    "agent": actor,
                                    "type": "program_catalog_materialized",
                                    "task_id": profile["expected_dispatch_contract"][
                                        "bootstrap_task_id"
                                    ],
                                    "message": (
                                        "Atomically admitted current-proof remediation "
                                        f"frontier ({len(final_plan['create'])} tasks)."
                                    ),
                                    "program_id": catalog["program_id"],
                                    "catalog_sha256": final_plan["catalog_sha256"],
                                    "admitted_task_ids": final_plan["create"],
                                    "admission_id": prepared_archive_path.stem,
                                    "admission_archive": str(prepared_archive_path),
                                    "admission_archive_state": "prepared",
                                    "readiness_sha256": readiness["sha256"],
                                }
                            )
                            ai_status.sync_all(working, refresh_views=False)
                            committed_state = deepcopy(working)
        if committed_state is not None:
            ai_status.refresh_derived_status_views_if_current(committed_state)
    assert final_plan is not None and readiness is not None
    admitted = list(final_plan["create"])
    readback_ids = admitted or [
        task_id
        for task_id in final_plan["exact"]
        if task_id in {task["id"] for task in tasks if task["wave"] == "G1"}
    ]
    readback = verify_current_canonical_readback(
        catalog=catalog,
        tasks=tasks,
        status_root=status_root,
        authority=authority,
        admitted_task_ids=readback_ids,
        readiness=readiness,
    )
    archive_path = prepared_archive_path
    if archive_ids and archive_path is not None:
        archive_path = finalize_current_admission_archive(
            path=archive_path,
            plan=final_plan,
            admitted_task_ids=archive_ids,
            command_runtime=command_runtime,
            actor=actor,
            readback=readback,
        )
    return {
        "created": admitted,
        "exact": final_plan["exact"],
        "deferred": final_plan["deferred"],
        "readiness_sha256": readiness["sha256"],
        "readback": readback,
        "admission_archive": str(archive_path) if archive_path else None,
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
            expected_contract = task_contract(task, catalog=catalog)
            active_contract = {key: active.get(key) for key in expected_contract}
            if active_contract != expected_contract:
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


def validate_catalog_file_binding(
    catalog_path: Path,
    catalog: dict[str, Any],
) -> None:
    profile = _current_profile(catalog)
    if profile is None:
        return
    try:
        file_sha256 = hashlib.sha256(catalog_path.read_bytes()).hexdigest()
    except OSError as exc:
        raise DispatchError(
            f"cannot hash current catalog source: {type(exc).__name__}"
        ) from exc
    if file_sha256 != profile["catalog_file_sha256"]:
        raise DispatchError(
            "current catalog file bytes do not match "
            f"PR #{profile['source_pr']} exact head"
        )
    if canonical_json_sha256(catalog) != profile["catalog_canonical_sha256"]:
        raise DispatchError("current catalog canonical digest is not exact")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--validate-only", action="store_true")
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--current",
        action="store_true",
        help="Use the reviewed 2026-08-03 corrected remediation profile.",
    )
    parser.add_argument(
        "--previous-current",
        action="store_true",
        help="Retain the reviewed 2026-07-31 current-proof remediation profile.",
    )
    parser.add_argument("--catalog", default=None)
    parser.add_argument(
        "--proof-ownership",
        default=str(DEFAULT_PROOF_OWNERSHIP_PATH),
    )
    parser.add_argument("--live-config", default=str(DEFAULT_LIVE_CONFIG_PATH))
    parser.add_argument(
        "--command-root",
        default=str(os.environ.get("PANTHEON_COMMAND_ROOT") or DEFAULT_COMMAND_ROOT),
    )
    parser.add_argument(
        "--command-sha",
        default=str(os.environ.get("PANTHEON_COMMAND_RUNTIME_SHA") or ""),
    )
    parser.add_argument("--readiness-config", default=None)
    parser.add_argument("--runtime-state", default=None)
    parser.add_argument("--provider-capabilities", default=None)
    args = parser.parse_args(argv)

    if sum(bool(value) for value in (args.current, args.previous_current, args.catalog)) > 1:
        parser.error("--current, --previous-current, and --catalog are mutually exclusive")
    catalog_path = Path(
        args.catalog
        or (
            DEFAULT_CURRENT_CATALOG_PATH
            if args.current
            else (
                DEFAULT_PREVIOUS_CURRENT_CATALOG_PATH
                if args.previous_current
                else DEFAULT_CATALOG_PATH
            )
        )
    ).resolve()
    catalog = load_json_object(catalog_path)
    tasks = validate_catalog(catalog)
    validate_catalog_file_binding(catalog_path, catalog)
    profile = _current_profile(catalog)
    is_current = profile is not None
    proof_ownership_path: Path | None = None
    delegations: list[dict[str, Any]] = []
    proof_ownership_sha256: str | None = None
    if not is_current:
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
        output: dict[str, Any] = {
            "status": "valid",
            "program_id": catalog["program_id"],
            "task_count": len(tasks),
            "catalog_sha256": canonical_json_sha256(catalog),
        }
        if is_current:
            output.update(
                {
                    "catalog_file_sha256": profile["catalog_file_sha256"],
                    "source_pr": profile["source_pr"],
                    "source_head": profile["source_head"],
                    "source_branch_ci_run": profile["source_branch_ci_run"],
                    "source_branch_ci_conclusion": "success",
                    "maximum_parallel_frontier_G1": 25,
                }
            )
        else:
            output.update(
                {
                    "proof_delegation_count": len(delegations),
                    "proof_ownership_sha256": proof_ownership_sha256,
                }
            )
        print(json.dumps(output, sort_keys=True))
        return 0

    status_root = Path(
        os.path.expanduser(os.environ.get("PANTHEON_STATUS_ROOT", str(REPO_ROOT)))
    ).resolve()
    authority = resolve_task_state_authority(
        Path(args.live_config),
        status_root=status_root,
    )
    task_state_snapshot = load_authoritative_task_snapshot(
        authority,
        refresh_checkpoint=not args.dry_run,
    )
    state = task_state_snapshot["state"]
    command_root = Path(args.command_root).resolve()
    readiness_config_path = Path(
        args.readiness_config or command_root / ".orchestrator" / "config.json"
    ).resolve()
    runtime_state_path = Path(
        args.runtime_state or status_root / ".orchestrator" / "state.json"
    ).resolve()
    provider_capabilities_path = Path(
        args.provider_capabilities
        or status_root / ".orchestrator" / "provider_capabilities.json"
    ).resolve()
    readiness = (
        load_current_readiness_snapshot(
            config_path=readiness_config_path,
            runtime_state_path=runtime_state_path,
            provider_capabilities_path=provider_capabilities_path,
        )
        if is_current
        else None
    )
    plan = plan_materialization(
        catalog,
        tasks,
        status_root=status_root,
        state=state,
        readiness=readiness,
    )
    if args.dry_run:
        output = {key: value for key, value in plan.items() if key != "create_tasks"}
        if is_current:
            output["readiness"] = readiness
        else:
            output["proof_ownership_sha256"] = proof_ownership_sha256
            output["proof_delegation_count"] = len(delegations)
        output["task_state_store"] = {
            "mode": authority["mode"],
            "event_log": str(authority["event_log"]),
            "snapshot": authoritative_snapshot_evidence(task_state_snapshot),
        }
        output["status"] = "dry_run"
        print(json.dumps(output, indent=2, sort_keys=True))
        return 0

    command_runtime = resolve_command_runtime(
        command_root,
        expected_sha=args.command_sha,
    )
    if Path(command_runtime["root"]).resolve() != REPO_ROOT.resolve():
        raise DispatchError(
            "--apply must execute the dispatcher and catalog from the exact "
            "installed command root"
        )
    installed_catalog = (
        Path(command_runtime["root"])
        / (
            profile["catalog_relative_path"]
            if profile is not None
            else "docs/bff/execution-tasks/2026-07-26-twelve-loop-gap/tasks.json"
        )
    ).resolve()
    if catalog_path != installed_catalog:
        raise DispatchError(
            f"--apply catalog must be the installed reviewed catalog: "
            f"{catalog_path} != {installed_catalog}"
        )
    if is_current:
        expected_config = Path(command_runtime["root"]) / ".orchestrator" / "config.json"
        expected_runtime_state = status_root / ".orchestrator" / "state.json"
        expected_capabilities = (
            status_root / ".orchestrator" / "provider_capabilities.json"
        )
        for label, actual, expected in (
            ("readiness config", readiness_config_path, expected_config),
            ("runtime state", runtime_state_path, expected_runtime_state),
            ("provider capabilities", provider_capabilities_path, expected_capabilities),
        ):
            if actual.resolve() != expected.resolve():
                raise DispatchError(
                    f"--apply {label} must use live canonical truth: "
                    f"{actual} != {expected}"
                )
        result = apply_current_materialization_atomic(
            catalog=catalog,
            tasks=tasks,
            status_root=status_root,
            authority=authority,
            command_runtime=command_runtime,
            config_path=readiness_config_path,
            runtime_state_path=runtime_state_path,
            provider_capabilities_path=provider_capabilities_path,
        )
    else:
        assert proof_ownership_path is not None
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
        result = {
            "created": created,
            "exact": plan["exact"],
        }
    print(
        json.dumps(
            {
                "status": "applied" if result["created"] else "replay_exact",
                "program_id": catalog["program_id"],
                "catalog_sha256": plan["catalog_sha256"],
                **result,
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
