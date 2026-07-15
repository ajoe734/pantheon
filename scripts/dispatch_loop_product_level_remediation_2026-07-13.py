#!/usr/bin/env python3
"""Validate and dispatch the 2026-07-13 loop product-level remediation DAG."""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import ModuleType
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
ALLOWED_FLEET_ACTORS = ["Codex", "Codex2"]
LIVE_ADMISSION_MARKER_FIELDS = {
    "active_worker",
    "attempt",
    "branch",
    "claim_token",
    "claimed_by",
    "current_attempt_id",
    "dispatch_token",
    "payload_signature",
    "provider",
    "worker_provider",
    "worker_slot",
    "dispatch_slot",
    "dispatch_slot_id",
    "queue_event_id",
    "task_worktree",
    "workspace_path",
    "declared_scope",
    "expected_branch",
    "workspace_branch",
    "admission_id",
    "admission_sha256",
    "lease_acquired_at",
    "lease_expires_at",
    "pr_number",
    "pr_url",
    "review_file",
    "admitted_merge_target",
    "admission",
    "runtime_admission",
    "dispatch_admission",
    "review_started_at",
    "remote",
    "run_id",
    "started_at",
    "task_signature",
    "worker",
    "worker_pid",
    "worker_run_id",
}
TASK_CONTRACT_FIELDS = REQUIRED_TASK_FIELDS - {"owner", "reviewer", "status", "next"}
ACTIVITY_OUTBOX_SCHEMA_VERSION = 4
ACTIVITY_EVENT_TYPES = {
    "assign",
    "catalog_migration",
    "completion_authority_install",
}
ACTIVITY_TRANSACTION_FIELDS = {
    "schema_version",
    "transaction_id",
    "program_id",
    "catalog_sha256",
    "actor",
    "actor_policy",
    "actor_policy_sha256",
    "created_at",
    "affected_state_projection",
    "affected_state_projection_sha256",
    "events",
}
ACTIVITY_COMMON_EVENT_FIELDS = {
    "event_id",
    "ordinal",
    "event_count",
    "ts",
    "agent",
    "type",
    "task_id",
    "message",
    "program_id",
    "catalog_sha256",
    "transaction_id",
    "actor_policy_sha256",
    "affected_state_projection_sha256",
}
ACTIVITY_EVENT_EXTRA_FIELDS = {
    "assign": {
        "assigned_owner",
        "assigned_reviewer",
        "task_contract_sha256",
        "source_ref_sha256",
        "created_at",
    },
    "catalog_migration": {
        "migration_id",
        "migration_record_sha256",
        "before_task_contract_sha256",
        "after_task_contract_sha256",
    },
    "completion_authority_install": {
        "completion_authority_sha256",
        "completion_overlay_sha256",
    },
}
EXPECTED_EXECUTION_AUTHORITY = {
    "planner_role": "plan_archive_dispatch_monitor_review_only",
    "implementation_role": "supervisor_admitted_fleet_worker",
    "review_role": "distinct_supervisor_admitted_fleet_reviewer",
    "planner_controller_identity": "/root",
    "planner_may_edit_declared_product_artifacts": False,
    "draft_input_policy": (
        "open draft PRs, local diffs, and unmerged worktrees are "
        "non-authoritative inputs that an admitted fleet may audit, adopt, "
        "rewrite, or discard"
    ),
    "required_worker_bindings": [
        "task_id",
        "run_id",
        "worker_provider",
        "worker_slot",
        "task_worktree",
        "declared_scope",
        "expected_branch",
        "remote",
        "merge_target",
    ],
    "formal_review_required": True,
    "owner_reviewer_must_be_distinct_runtime_identities": True,
}
EXPECTED_LOOP_SCOPE = {
    "canonical_l1_loop_ids": [
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
    ],
    "composite_overlay_ids": ["per_persona_ooda"],
    "final_authority_requires_exact_union": True,
    "each_loop_requires_non_close_product_level_task": True,
}
EXPECTED_COMPLETION_AUTHORITY = {
    "schema_version": 1,
    "task_id": "LOOP-PROD-CLOSE-002",
    "checkpoint_only_task_ids": ["LOOP-PROD-CLOSE-001"],
    "guard_install_task_id": "LOOP-PROD-SIGNOFF-001",
    "guard_direct_dependency_ids": [
        "LOOP-PROD-CLOSE-001",
        "LOOP-PROD-WORKER-001",
        "LOOP-PROD-ATTEST-001",
    ],
    "final_direct_dependency_ids": [
        "EVOCHAIN-011",
        "EVOLOOP-009",
        "EVOLOOP-011",
        "LOOP-PROD-CLOSE-001",
        "LOOP-PROD-DELIVERY-001",
        "LOOP-PROD-WORKER-001",
        "LOOP-PROD-LEASE-001",
        "LOOP-PROD-BROWSER-AUTH-001",
        "LOOP-PROD-FLEET-001",
        "LOOP-PROD-ATTEST-001",
        "LOOP-PROD-AUTH-OPS-001",
        "LOOP-PROD-FE-EVID-001",
        "LOOP-PROD-FE-BUILD-001",
        "LOOP-PROD-SIGNOFF-001",
    ],
    "required_human_ops_signoff_task_ids": [
        "LOOP-PROD-PPL-001",
        "LOOP-PROD-TJ-003",
        "LOOP-PROD-PINT-001",
        "LOOP-PROD-MAI-003",
        "LOOP-PROD-CLOSE-001",
        "LOOP-PROD-AUTH-BOOT-001",
        "LOOP-PROD-BROWSER-AUTH-001",
        "LOOP-PROD-AUTH-OPS-001",
        "LOOP-PROD-CLOSE-002",
    ],
    "role_resolution": "catalog_bound_program_overlay",
    "pre_guard_done_semantics": (
        "checkpoint_only_pending_exact_final_reverification"
    ),
    "live_overlay_state_key": "program_completion_authorities",
    "live_overlay_roles": {
        "LOOP-PROD-CLOSE-001": "checkpoint_only",
        "LOOP-PROD-SIGNOFF-001": "guard_installer",
        "LOOP-PROD-CLOSE-002": "final_authority",
    },
    "checkpoint_consumption_required": True,
    "dispatcher_pre_completion_policy": (
        "reject_preexisting_consumption_or_program_completed"
    ),
    "consumption_writer_task_id": "LOOP-PROD-CLOSE-002",
    "checkpoint_consumption_state_key": "program_completion_checkpoint_consumptions",
    "checkpoint_consumption_record_contract": {
        "schema_version": 1,
        "checkpoint_task_id": "LOOP-PROD-CLOSE-001",
        "guard_task_id": "LOOP-PROD-SIGNOFF-001",
        "consumer_task_id": "LOOP-PROD-CLOSE-002",
        "append_only": True,
        "single_consumption_per_program_catalog_checkpoint": True,
        "protected_human_ops_required": True,
        "required_binding_fields": [
            "program_id",
            "catalog_sha256",
            "completion_authority_sha256",
            "completion_overlay_sha256",
            "checkpoint_task_id",
            "checkpoint_task_contract_sha256",
            "checkpoint_evidence_manifest_sha256",
            "checkpoint_verdict_sha256",
            "guard_task_id",
            "guard_activation_sha256",
            "consumer_task_id",
            "consumer_task_contract_sha256",
            "final_verdict_sha256",
            "verdict_id",
            "guard_verifier_capability_sha256",
            "signature_algorithm",
            "key_id",
            "policy_version",
            "signature",
            "revocation_checked_at",
            "ledger_entry_id",
            "actor_id",
            "actor_role",
            "consumed_at",
            "nonce",
        ],
    },
    "requires_protected_human_ops_verdict": True,
    "verdict_binding_fields": [
        "program_id",
        "catalog_sha256",
        "task_id",
        "closeout_manifest_sha256",
        "target_environment",
        "frontend_sha",
        "bff_sha",
        "attestation_policy",
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
EXPECTED_AUTH_LIFECYCLE = {
    "schema_version": 1,
    "initial_provisioning_authority_task_id": "LOOP-PROD-AUTH-BOOT-001",
    "strict_auth_task_id": "LOOP-PROD-AUTH-001",
    "lease_task_id": "LOOP-PROD-LEASE-001",
    "attestation_task_id": "LOOP-PROD-ATTEST-001",
    "credential_lifecycle_task_id": "LOOP-PROD-AUTH-OPS-001",
    "browser_activation_task_id": "LOOP-PROD-BROWSER-AUTH-001",
    "bootstrap_proof_type": "protected_external_provisional_record",
    "bootstrap_requires_program_attestation": False,
    "strict_auth_prebootstrap_semantics": (
        "strict_auth_code_and_non_pristine_state_may_be_delivered_or_preserved_"
        "independently; hosted_qualification_lease_lifecycle_and_browser_"
        "activation_require_bootstrap"
    ),
    "credential_lifecycle_may_create_initial_credentials": False,
    "required_direct_dependencies": {
        "LOOP-PROD-AUTH-BOOT-001": [
            "LOOP-PROD-002",
            "LOOP-PROD-DELIVERY-001",
        ],
        "LOOP-PROD-LEASE-001": [
            "LOOP-PROD-AUTH-BOOT-001",
            "LOOP-PROD-AUTH-001",
            "LOOP-PROD-WORKER-001",
        ],
        "LOOP-PROD-ATTEST-001": [
            "LOOP-PROD-002",
            "LOOP-PROD-WORKER-001",
            "LOOP-PROD-LEASE-001",
        ],
        "LOOP-PROD-AUTH-OPS-001": [
            "LOOP-PROD-AUTH-BOOT-001",
            "LOOP-PROD-AUTH-001",
            "LOOP-PROD-LEASE-001",
            "LOOP-PROD-ATTEST-001",
        ],
        "LOOP-PROD-BROWSER-AUTH-001": [
            "LOOP-PROD-AUTH-BOOT-001",
            "LOOP-PROD-AUTH-001",
            "LOOP-PROD-FE-001",
            "LOOP-PROD-DELIVERY-001",
            "LOOP-PROD-LEASE-001",
            "LOOP-PROD-AUTH-OPS-001",
        ],
    },
    "credential_lineage_binding_fields": [
        "environment",
        "bootstrap_record_sha256",
        "authorization_event_id",
        "secret_version_ids",
        "identity_profile_ids",
        "policy_version",
        "issued_at",
        "expires_or_review_at",
        "revocation_path",
    ],
}
EXPECTED_DISPATCH_PREREQUISITE = {
    "task_id": "LOOP-PROD-RUNTIME-BOOT-001",
    "task_doc": (
        "docs/bff/execution-tasks/2026-07-13-loop-product-level-remediation/"
        "LOOP-PROD-RUNTIME-BOOT-001.md"
    ),
    "task_contract_fixture": (
        "docs/bff/execution-tasks/2026-07-13-loop-product-level-remediation/"
        "fixtures/runtime-lock-bootstrap-task.v1.json"
    ),
    "task_contract_fixture_sha256": (
        "eee36ad3bf27375805bcaf8379117cf409a00d039d8dde8c0387754961ee17a2"
    ),
    "task_contract_sha256": (
        "04f382e320292e11df3b4668ec4383819b9c9abadcc48f3b9150a7abcb65141e"
    ),
    "required_status": "done",
    "must_preexist_primary_materialization": True,
    "protocol": {
        "schema_version": 1,
        "protocol_id": "pantheon-runtime-task-audit-lock-v1",
        "capability_manifest": (
            ".orchestrator/runtime-task-audit-lock-capability.json"
        ),
        "lock_order": ["runtime_admission", "task_state", "activity_audit"],
        "stable_lock_paths": [
            ".orchestrator/runtime-admission.lock",
            ".orchestrator/task-state.lock",
            ".orchestrator/activity-audit.lock",
        ],
        "required_api": [
            "tasks_runtime_admission_guard",
            "canonical_task_state_lock_file",
            "activity_audit_lock_file",
            "verify_runtime_lock_capability",
        ],
        "shared_read_supported": True,
        "admission_decision_contract": {
            "schema_version": 1,
            "required_fields": [
                "schema_version",
                "protocol_id",
                "strict",
                "lock_mode",
                "task_ids",
                "source_sha256",
                "conflicts",
                "allowed",
                "reason_id",
                "snapshot_sha256",
            ],
            "source_ids": [
                "runtime_state",
                "event_queue",
                "approval_queue",
            ],
            "snapshot_algorithm": "sha256(canonical-json(source_sha256))",
            "clear_reason_id": "clear",
            "conflict_statuses": [
                "queued",
                "started",
                "running",
                "waiting_approval",
                "suspended_approval",
                "manual_pending",
                "retry_backoff",
                "stalled",
                "fallback",
                "admitted",
            ],
            "missing_empty_malformed_unreadable_or_foreign_source_policy": (
                "reject"
            ),
        },
        "capability_manifest_required_fields": [
            "schema_version",
            "protocol_id",
            "module_path",
            "lock_order",
            "stable_lock_paths",
            "shared_read_supported",
            "api",
            "writers",
            "writer_registry_path",
            "writer_registry_sha256",
            "dispatcher_sha256",
            "bootstrap_task_id",
            "bootstrap_task_contract_sha256",
            "bootstrap_completion_evidence_path",
            "bootstrap_completion_evidence_sha256",
            "merged_commit_sha",
        ],
        "writer_registry_path": (
            ".orchestrator/runtime-task-audit-writer-registry.json"
        ),
        "required_writer_paths": [
            ".orchestrator/runtime_state.py",
            ".orchestrator/supervisor.py",
            ".orchestrator/common.py",
            ".orchestrator/approval_queue.py",
            ".orchestrator/adapters/file_inbox.py",
            ".orchestrator/watch_events.py",
            ".orchestrator/supervisor_watchdog.py",
            "scripts/ai_status.py",
            "scripts/dispatch_loop_product_level_remediation_2026-07-13.py",
        ],
    },
}
EXPECTED_CONTRACT_FIXTURES = {
    "browser_auth_incidents_v1": {
        "path": (
            "docs/bff/execution-tasks/2026-07-13-loop-product-level-remediation/"
            "fixtures/browser-auth-incidents.v1.json"
        ),
        "sha256": "71038929281e844b26a3d8ba6c48f167a94b9d6281183dbdf45f2627b549eb19",
        "schema_version": 1,
        "fixture_set_id": "loop-product-browser-auth-incidents-v1",
        "required_fixture_ids": [
            "pantheon-pr-3557-bff-first-public-viewer-lockdown",
            "execute-plans-pr-323-fe-first-fixed-public-viewer",
            "pantheon-pr-3587-effective-3557-revert",
            "pantheon-pr-3588-zero-tree-duplicate-3557-revert",
        ],
        "required_by": [
            "LOOP-PROD-DELIVERY-001",
            "LOOP-PROD-BROWSER-AUTH-001",
            "LOOP-PROD-CLOSE-002",
        ],
    },
    "browser_auth_route_matrix_v1": {
        "path": (
            "docs/bff/execution-tasks/2026-07-13-loop-product-level-remediation/"
            "fixtures/browser-auth-route-matrix.v1.json"
        ),
        "sha256": "8e465fc657e09e8be982181de5fd5929d2719392fcac472245df2c30563d3531",
        "schema_version": 1,
        "fixture_set_id": "loop-product-browser-auth-route-matrix-v1",
        "required_fixture_ids": [
            "viewer-cookie-me-get",
            "viewer-cookie-strategies-get",
            "viewer-cookie-personas-get",
            "viewer-cookie-capital-pools-get",
            "viewer-cookie-rebalances-get",
            "viewer-cookie-deployments-get",
            "viewer-cookie-jobs-get",
            "viewer-cookie-alerts-get",
            "viewer-cookie-incidents-get",
            "viewer-cookie-audit-get",
            "viewer-cookie-artifacts-get",
            "viewer-cookie-runtimes-get",
            "viewer-cookie-mcp-servers-get",
            "viewer-cookie-mcp-tools-get",
            "viewer-cookie-skills-get",
            "viewer-cookie-channels-get",
            "viewer-cookie-tools-get",
            "viewer-cookie-ranking-formulas-get",
            "viewer-cookie-research-experiments-get",
            "viewer-cookie-agora-signals-get",
            "viewer-cookie-agora-inbox-get",
            "viewer-cookie-agora-journal-get",
            "viewer-cookie-agora-postmortems-get",
            "viewer-cookie-v5-loop-runs-get",
            "viewer-cookie-v5-sentinel-findings-get",
            "viewer-cookie-v5-interventions-get",
            "viewer-cookie-v5-execution-persona-health-get",
            "viewer-cookie-shell-summary-get",
            "viewer-cookie-assistant-mode-get",
            "viewer-cookie-assistant-providers-get",
            "viewer-cookie-assistant-provider-usage-summary-get",
            "viewer-cookie-assistant-orchestrator-status-get",
            "viewer-cookie-assistant-dev-docs-packet-get",
            "viewer-cookie-management-ai-conversations-get",
            "viewer-cookie-management-ai-conversation-get",
            "viewer-cookie-sse-replay-get",
            "viewer-cookie-agora-ask-sessions-get",
            "viewer-cookie-agora-daily-get",
            "viewer-cookie-channels-param-get",
            "viewer-cookie-capital-pools-param-get",
            "viewer-cookie-ranking-formulas-param-get",
            "viewer-cookie-rebalances-param-get",
            "viewer-cookie-artifacts-param-get",
            "viewer-cookie-evolution-programs-get",
            "viewer-cookie-evolution-programs-param-get",
            "viewer-cookie-jobs-param-get",
            "viewer-cookie-research-experiments-param-get",
            "viewer-cookie-v5-control-room-get",
            "viewer-cookie-v5-execution-strategy-health-get",
            "viewer-cookie-v5-loop-runs-param-get",
            "viewer-cookie-v5-sentinel-findings-param-get",
            "viewer-frontend-actions-param-param-param-post-deny",
            "viewer-frontend-confirm-tokens-param-delete-deny",
            "viewer-cookie-alerts-param-get",
            "viewer-cookie-approvals-get",
            "viewer-cookie-approvals-param-get",
            "viewer-cookie-confirm-tokens-param-get",
            "viewer-cookie-deployments-param-get",
            "viewer-cookie-incidents-param-get",
            "viewer-cookie-runtimes-param-get",
            "viewer-frontend-alerts-param-acknowledge-post-deny",
            "viewer-frontend-approvals-param-decide-post-deny",
            "viewer-frontend-confirm-tokens-post-deny",
            "viewer-frontend-confirm-tokens-param-redeem-post-deny",
            "viewer-cookie-mcp-servers-param-get",
            "viewer-cookie-mcp-tools-param-get",
            "viewer-cookie-personas-param-get",
            "viewer-cookie-strategies-param-get",
            "viewer-cookie-skills-param-get",
            "viewer-cookie-tools-param-get",
            "viewer-cookie-v5-interventions-param-get",
            "viewer-frontend-v5-interventions-param-decide-post-deny",
            "viewer-frontend-v5-interventions-param-remediate-post-deny",
            "viewer-cookie-me-head-router-negative",
            "viewer-cookie-logout-post",
            "viewer-cookie-logout-replay-idempotent",
            "fixed-viewer-bearer-logout-deny",
            "viewer-refresh-cookie-post",
            "fixed-viewer-bearer-refresh-deny",
            "post-logout-me-deny",
            "post-logout-refresh-family-deny",
            "viewer-refresh-cookie-replay-deny",
            "expired-viewer-cookie-me-deny",
            "wrong-origin-viewer-me-deny",
            "wrong-origin-viewer-refresh-deny",
            "missing-csrf-viewer-refresh-deny",
            "wrong-csrf-viewer-logout-deny",
            "viewer-refresh-role-upgrade-deny",
            "viewer-refresh-capability-upgrade-deny",
            "anonymous-sse-liveness-get",
            "viewer-query-token-sse-deny",
            "fixed-viewer-bearer-sse-deny",
            "expired-viewer-cookie-sse-deny",
            "wrong-origin-viewer-cookie-sse-deny",
            "viewer-cookie-sse-duplicate-replay-ids-fail",
            "mixed-bearer-cookie-me-deny",
            "raw-literal-viewer-cookie-me-deny",
            "raw-literal-viewer-body-refresh-deny",
            "near-match-viewer-subject-me-deny",
            "method-override-viewer-logout-deny",
            "exact-origin-viewer-preflight-allow",
            "cross-origin-viewer-preflight-deny",
            "viewer-control-mode-activate-deny",
            "viewer-control-mode-deactivate-deny",
            "viewer-control-mode-passphrase-deny",
            "viewer-repair-worktree-deny",
            "viewer-tools-preview-deny",
            "viewer-tools-validate-deny",
            "viewer-tools-execute-deny",
            "viewer-dev-docs-generate-deny",
            "viewer-dev-bridge-task-packet-deny",
            "viewer-management-ask-deny",
            "viewer-management-ask-stream-deny",
            "viewer-approval-decide-deny",
            "viewer-approval-batch-decide-deny",
            "viewer-v5-intervention-decide-deny",
            "viewer-incident-rollback-deployment-deny",
            "viewer-v1-command-deny",
        ],
        "required_by": [
            "LOOP-PROD-BROWSER-AUTH-001",
            "LOOP-PROD-CLOSE-002",
        ],
    },
}
EXPECTED_INCIDENT_PROJECTION_FIELDS = [
    "fixture_id",
    "semantic_intent_key",
    "repository",
    "pr",
    "expected_replay",
]
EXPECTED_INCIDENT_PROJECTION_SHA256 = (
    "358c9052afd1bc671a05bcedd39bd896df4599ace1b499d2809449d0344a7628"
)
EXPECTED_ROUTE_ROW_KEY_FIELDS = [
    "method",
    "path_template",
    "frontend_callsite",
    "identity_profile",
    "transport_profile",
    "origin_profile",
    "cookie_profile",
    "attack_classes",
]
EXPECTED_PREIMAGE_FIXTURE_PATH = (
    "docs/bff/execution-tasks/2026-07-13-loop-product-level-remediation/"
    "fixtures/catalog-v1-migration-preimages.json"
)
EXPECTED_PREIMAGE_FIXTURE_SHA256 = (
    "91c622e5f586cf9b94cf810031603fba32d6f25186b58e7ebe3bd2c3f6a04956"
)
EXPECTED_PREIMAGE_MUTABLE_LIVE_FIELDS = [
    "owner",
    "reviewer",
    "next",
    "created_at",
    "last_update",
]
REQUIRED_LOOP_IDS = set(EXPECTED_LOOP_SCOPE["canonical_l1_loop_ids"]) | set(
    EXPECTED_LOOP_SCOPE["composite_overlay_ids"]
)
REQUIRED_AUTHORITY_DISPATCH_RULES = {
    "the planning and dispatch controller may plan, archive, dispatch, monitor, and review only; it must not implement any declared product artifact",
    "implementation may be performed only by a supervisor-admitted fleet worker bound to the exact task, run, provider, slot, clean worktree, scope, branch, remote, and merge target",
    "owner and reviewer must be distinct admitted fleet runtime identities; a self-authored trailer, same-session subagent note, or planner review is not independent review",
    "open draft PRs, local diffs, and unmerged worktrees are inputs only; the admitted fleet must audit the exact head and may adopt, rewrite, or discard them",
}


class DispatchError(RuntimeError):
    """Fail-closed packet or live-state validation error."""


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DispatchError(f"duplicate JSON key is forbidden: {key}")
        result[key] = value
    return result


def strict_json_loads(raw: str | bytes, *, source: str) -> Any:
    try:
        return json.loads(raw, object_pairs_hook=_reject_duplicate_json_keys)
    except DispatchError:
        raise
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise DispatchError(f"invalid JSON in {source}: {exc}") from exc


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
        raw = path.read_bytes()
    except FileNotFoundError as exc:
        raise DispatchError(f"JSON file not found: {path}") from exc
    except OSError as exc:
        raise DispatchError(f"JSON file is unreadable: {path}") from exc
    payload = strict_json_loads(raw, source=str(path))
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


def task_contract_sha256(task: dict[str, Any]) -> str:
    """Bind immutable catalog fields while allowing normal runtime ownership/state."""

    return canonical_json_sha256(
        {field: task.get(field) for field in sorted(TASK_CONTRACT_FIELDS)}
    )


def execution_authority_sha256(catalog: dict[str, Any]) -> str:
    return canonical_json_sha256(catalog.get("execution_authority"))


def completion_authority_sha256(catalog: dict[str, Any]) -> str:
    return canonical_json_sha256(catalog.get("completion_authority"))


def auth_lifecycle_sha256(catalog: dict[str, Any]) -> str:
    return canonical_json_sha256(catalog.get("auth_lifecycle"))


def contract_fixtures_sha256(catalog: dict[str, Any]) -> str:
    return canonical_json_sha256(catalog.get("contract_fixtures"))


def _content_addressed_json(path: Path, expected_sha256: str) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise DispatchError(f"content-addressed fixture is unreadable: {path}") from exc
    if sha256_bytes(raw) != expected_sha256:
        raise DispatchError(f"content-addressed fixture digest mismatch: {path}")
    payload = strict_json_loads(raw, source=str(path))
    if not isinstance(payload, dict):
        raise DispatchError(f"content-addressed fixture must be an object: {path}")
    return payload


def _incident_git_text(*args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(REPO_ROOT), *args],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise DispatchError("browser incident Git object verification is unavailable") from exc
    if result.returncode != 0:
        raise DispatchError(
            "browser incident Git object verification failed: "
            + " ".join(args[:2])
        )
    return result.stdout.strip()


def validate_pantheon_incident_git_objects(rows: list[dict[str, Any]]) -> None:
    for row in rows:
        if row.get("repository") != "ajoe734/pantheon":
            continue
        fixture_id = str(row.get("fixture_id") or "")
        pr = row.get("pr") or {}
        head_sha = str(pr.get("head_sha") or "")
        merge_sha = str(pr.get("merge_sha") or "")
        if not _is_lower_hex(head_sha, 40) or not _is_lower_hex(merge_sha, 40):
            raise DispatchError(f"{fixture_id} incident commit identity is invalid")
        head_tree = _incident_git_text("show", "-s", "--format=%T", head_sha)
        head_parents = _incident_git_text(
            "show", "-s", "--format=%P", head_sha
        ).split()
        merge_tree = _incident_git_text("show", "-s", "--format=%T", merge_sha)
        merge_parents = _incident_git_text(
            "show", "-s", "--format=%P", merge_sha
        ).split()
        if (
            head_tree != pr.get("head_tree_sha")
            or head_parents != pr.get("head_parent_shas")
            or merge_tree != pr.get("merge_tree_sha")
            or merge_parents != pr.get("merge_parent_shas")
        ):
            raise DispatchError(
                f"{fixture_id} incident Git commit tree/parent projection is false"
            )
        blobs = pr.get("postimage_blobs")
        if not isinstance(blobs, list) or not blobs:
            raise DispatchError(f"{fixture_id} incident postimage blob set is missing")
        for blob in blobs:
            if not isinstance(blob, dict) or set(blob) != {"path", "sha"}:
                raise DispatchError(f"{fixture_id} incident postimage blob is invalid")
            path = Path(str(blob["path"]))
            if path.is_absolute() or ".." in path.parts:
                raise DispatchError(f"{fixture_id} incident postimage path is invalid")
            actual_blob = _incident_git_text(
                "rev-parse",
                f"{head_sha}:{blob['path']}",
            )
            if actual_blob != blob["sha"]:
                raise DispatchError(
                    f"{fixture_id} incident postimage blob projection is false"
                )


def validate_browser_incident_fixture(
    payload: dict[str, Any],
    reference: dict[str, Any],
) -> None:
    rows = payload.get("fixtures")
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise DispatchError("browser incident fixtures must be an exact object list")
    ids = [str(row.get("fixture_id")) for row in rows]
    if ids != reference["required_fixture_ids"] or len(ids) != len(set(ids)):
        raise DispatchError("browser incident fixture IDs are not exact")
    fields = payload.get("immutable_projection_fields")
    if fields != EXPECTED_INCIDENT_PROJECTION_FIELDS:
        raise DispatchError("browser incident immutable projection fields are not exact")
    projection = [{field: row.get(field) for field in fields} for row in rows]
    if (
        payload.get("immutable_projection_sha256")
        != EXPECTED_INCIDENT_PROJECTION_SHA256
        or canonical_json_sha256(projection) != EXPECTED_INCIDENT_PROJECTION_SHA256
    ):
        raise DispatchError("browser incident immutable PR/tree/replay projection changed")
    validate_pantheon_incident_git_objects(rows)
    by_id = {str(row["fixture_id"]): row for row in rows}
    duplicate = by_id["pantheon-pr-3588-zero-tree-duplicate-3557-revert"]
    effective = by_id["pantheon-pr-3587-effective-3557-revert"]
    if (
        duplicate["pr"]["head_tree_sha"] != effective["pr"]["head_tree_sha"]
        or duplicate["pr"]["merge_tree_sha"]
        != duplicate["pr"]["head_tree_sha"]
        or duplicate["pr"]["merge_tree_sha"] != effective["pr"]["merge_tree_sha"]
        or duplicate["expected_replay"]["effective_tree_delta"] is not False
        or duplicate["expected_replay"]["second_deploy_forbidden"] is not True
    ):
        raise DispatchError("3587/3588 duplicate-repair graph semantics changed")
    diagnosis = payload.get("diagnosis") or {}
    if (
        diagnosis.get("reason_id") != "AUTH_PUBLIC_BROWSER_ENVIRONMENT_FORBIDDEN"
        or "does not prove" not in str(diagnosis.get("not_proven") or "")
    ):
        raise DispatchError("hosted browser incident diagnosis is not exact")
    archival = payload.get("evidence_archival_contract") or {}
    if (
        archival.get("status")
        != "fleet_must_vendor_redacted_extracts_before_closeout"
        or archival.get("index_path")
        != "docs/bff/execution-tasks/2026-07-13-loop-product-level-remediation/fixtures/evidence/browser-auth-incidents/run-29298450774/index.v1.json"
        or archival.get("required_file_ids")
        != [
            "bff-authenticated-live-smoke",
            "management-live-deep-validation",
            "playwright-results",
            "release-gate-summary",
            "historical-get-results",
            "sse-replay-results",
        ]
        or archival.get("network_independent_replay_required") is not True
        or archival.get("redaction_and_secret_scan_required") is not True
    ):
        raise DispatchError("browser incident evidence archival contract is not exact")
    for row in rows:
        for case in row.get("request_cases") or []:
            if (
                not isinstance(case, dict)
                or not isinstance((case.get("expected") or {}).get("http_status"), int)
            ):
                raise DispatchError(
                    "incident replay must use one exact HTTP status, not alternatives"
                )


def validate_browser_route_fixture(
    payload: dict[str, Any],
    reference: dict[str, Any],
) -> None:
    rows = payload.get("rows")
    if not isinstance(rows, list) or not rows or not all(
        isinstance(row, dict) for row in rows
    ):
        raise DispatchError("browser route matrix rows must be a non-empty object list")
    row_ids = [str(row.get("row_id") or "") for row in rows]
    if (
        row_ids != payload.get("required_row_ids")
        or row_ids != reference["required_fixture_ids"]
        or not all(row_ids)
        or len(row_ids) != len(set(row_ids))
    ):
        raise DispatchError("browser route matrix row-ID union is not exact")
    if payload.get("required_row_key_fields") != EXPECTED_ROUTE_ROW_KEY_FIELDS:
        raise DispatchError("browser route matrix row-key fields are not exact")
    required_generated = ["coverage_tags", "router_reached", "evidence_refs"]
    profiles = {
        "identity_profile": payload.get("identity_profiles"),
        "transport_profile": payload.get("transport_profiles"),
        "origin_profile": payload.get("origin_profiles"),
        "cookie_profile": payload.get("cookie_profiles"),
    }
    if any(not isinstance(values, dict) or not values for values in profiles.values()):
        raise DispatchError("browser route matrix profile dictionaries are incomplete")
    computed_keys: list[str] = []
    computed_pairs: set[tuple[str, str]] = set()
    attack_union: set[str] = set()
    for row in rows:
        if any(field not in row for field in EXPECTED_ROUTE_ROW_KEY_FIELDS):
            raise DispatchError("browser route matrix row-key projection is incomplete")
        if any(field not in row for field in required_generated):
            raise DispatchError("browser route matrix generated-row fields are incomplete")
        if (
            not isinstance(row["coverage_tags"], list)
            or not row["coverage_tags"]
            or len(row["coverage_tags"]) != len(set(map(str, row["coverage_tags"])))
            or not isinstance(row["router_reached"], bool)
            or not isinstance(row["evidence_refs"], list)
            or not row["evidence_refs"]
            or not isinstance(row["attack_classes"], list)
            or len(row["attack_classes"])
            != len(set(map(str, row["attack_classes"])))
        ):
            raise DispatchError("browser route matrix row evidence or attack shape is invalid")
        method = str(row["method"])
        path = str(row["path_template"])
        if method != method.upper() or not path.startswith("/") or "*" in path:
            raise DispatchError("browser route matrix method/path identity is invalid")
        for field, values in profiles.items():
            if row[field] not in values:
                raise DispatchError(f"browser route matrix references unknown {field}")
        computed_keys.append(
            json.dumps(
                {field: row[field] for field in EXPECTED_ROUTE_ROW_KEY_FIELDS},
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        )
        computed_pairs.add((method, path))
        attack_union.update(map(str, row["attack_classes"]))
    if payload.get("required_row_keys") != sorted(computed_keys) or len(
        computed_keys
    ) != len(set(computed_keys)):
        raise DispatchError("browser route matrix exact row-key union changed")
    expected_pairs = [
        {"method": method, "path_template": path}
        for method, path in sorted(computed_pairs)
    ]
    if payload.get("required_path_method_pairs") != expected_pairs:
        raise DispatchError("browser route matrix method/path union changed")
    universe = payload.get("route_universe") or {}
    if (
        universe.get("coverage_rule")
        != "exact_row_key_union_equals_required_row_keys"
        or universe.get("coverage_status") != "complete"
        or universe.get("missing_or_extra_rows_fail") is not True
        or universe.get("wildcards_allowed") is not False
        or universe.get("closeout_requires_coverage_status") != "complete"
        or universe.get("required_generated_row_fields") != required_generated
        or universe.get("pinned_frontend_manifest_entry_count") != 63
    ):
        raise DispatchError("browser route matrix exact coverage contract changed")
    required_get_paths = payload.get("required_incident_get_paths")
    historical_rows = [
        row for row in rows if "historical_incident_get" in row["coverage_tags"]
    ]
    if (
        not isinstance(required_get_paths, list)
        or len(required_get_paths) != 27
        or len(set(required_get_paths)) != 27
        or [row["path_template"] for row in historical_rows] != required_get_paths
        or any(row["method"] != "GET" for row in historical_rows)
    ):
        raise DispatchError("browser route matrix historical 27-GET union is not exact")
    required_privileged = payload.get("required_privileged_negative_row_ids")
    privileged_rows = [
        row for row in rows if "privileged_negative" in row["coverage_tags"]
    ]
    if (
        not isinstance(required_privileged, list)
        or len(required_privileged) != 16
        or len(set(required_privileged)) != 16
        or [row["row_id"] for row in privileged_rows] != required_privileged
        or any((row.get("expected") or {}).get("product_success") is not False for row in privileged_rows)
    ):
        raise DispatchError("browser route matrix privileged-negative union is not exact")
    required_attacks = payload.get("required_attack_classes")
    if (
        not isinstance(required_attacks, list)
        or len(required_attacks) != 13
        or len(set(required_attacks)) != 13
        or attack_union != set(required_attacks)
    ):
        raise DispatchError("browser route matrix attack-class union is not exact")
    frontend_rows = [
        row for row in rows if "frontend_manifest_callsite" in row["coverage_tags"]
    ]
    frontend_keys = [f"{row['method']} {row['path_template']}" for row in frontend_rows]
    if (
        len(frontend_rows) != 63
        or sorted(frontend_keys)
        != sorted(payload.get("required_frontend_manifest_route_keys") or [])
        or len(frontend_keys) != len(set(frontend_keys))
    ):
        raise DispatchError("browser route matrix pinned frontend manifest union changed")
    management_paths = payload.get("required_management_ai_get_paths")
    if not isinstance(management_paths, list) or any(
        ("GET", path) not in computed_pairs for path in management_paths
    ):
        raise DispatchError("browser route matrix Management AI GET union is incomplete")
    paths = {str(row["path_template"]) for row in rows}
    if "/bff/dashboard/summary" in paths or "/bff/management/shell-summary" not in paths:
        raise DispatchError("browser route matrix uses a nonexistent boot route")
    transports = payload["transport_profiles"]
    if not {"raw_literal_cookie", "credential_shaped_json_body"}.issubset(transports):
        raise DispatchError("browser route matrix raw-credential transports are incomplete")
    by_id = {str(row["row_id"]): row for row in rows}
    if (
        by_id["viewer-cookie-me-head-router-negative"]["expected"]["product_success"]
        is not False
        or by_id["anonymous-sse-liveness-get"]["expected"]["product_success"]
        is not False
        or by_id["viewer-cookie-sse-replay-get"]["expected"]["product_success"]
        is not True
    ):
        raise DispatchError("browser auth, router, liveness, and product outcomes are conflated")
    logout = (payload.get("session_lifecycle_contract") or {}).get("logout") or {}
    logout_row = by_id.get("viewer-cookie-logout-post") or {}
    logout_expected = logout_row.get("expected") or {}
    if (
        logout.get("method") != "POST"
        or logout.get("path_template") != "/bff/logout"
        or logout.get("success_http_status") != 200
        or logout.get("success_content_type") != "application/json"
        or logout.get("success_json_body") != {"status": "logged_out"}
        or logout.get("clear_cookie_profiles")
        != ["viewer_session", "viewer_refresh"]
        or logout.get("revoke_scope") != "exact_authenticated_refresh_family"
        or logout.get("post_logout_denial_row_ids")
        != ["post-logout-me-deny", "post-logout-refresh-family-deny"]
        or logout_row.get("method") != "POST"
        or logout_expected.get("router_status") != 200
        or logout_expected.get("content_type") != "application/json"
        or logout_expected.get("json_body") != {"status": "logged_out"}
        or logout_expected.get("state_delta")
        != "revoke_exact_access_session_and_refresh_family_then_clear_both_cookies"
        or (by_id.get("post-logout-me-deny", {}).get("expected") or {}).get("router_status")
        != 401
        or (by_id.get("post-logout-refresh-family-deny", {}).get("expected") or {}).get("router_status")
        != 401
    ):
        raise DispatchError("browser route matrix logout/refresh lifecycle is not exact")


def validate_contract_fixtures(catalog: dict[str, Any]) -> None:
    refs = catalog.get("contract_fixtures")
    if refs != EXPECTED_CONTRACT_FIXTURES:
        raise DispatchError(
            "catalog contract_fixtures must exactly preserve the reviewed incident "
            "and browser-route contracts"
        )
    for fixture_id, reference in EXPECTED_CONTRACT_FIXTURES.items():
        path = REPO_ROOT / reference["path"]
        payload = _content_addressed_json(path, reference["sha256"])
        if payload.get("schema_version") != reference["schema_version"]:
            raise DispatchError(f"{fixture_id} schema version mismatch")
        actual_set_id = payload.get("fixture_set_id") or payload.get("matrix_id")
        if actual_set_id != reference["fixture_set_id"]:
            raise DispatchError(f"{fixture_id} fixture-set identity mismatch")
        if fixture_id == "browser_auth_incidents_v1":
            validate_browser_incident_fixture(payload, reference)
        else:
            validate_browser_route_fixture(payload, reference)


def validate_preimage_fixture(migration: dict[str, Any]) -> dict[str, Any]:
    if (
        migration.get("preimage_fixture") != EXPECTED_PREIMAGE_FIXTURE_PATH
        or migration.get("preimage_fixture_sha256")
        != EXPECTED_PREIMAGE_FIXTURE_SHA256
    ):
        raise DispatchError("catalog migration preimage fixture reference is not exact")
    payload = _content_addressed_json(
        REPO_ROOT / EXPECTED_PREIMAGE_FIXTURE_PATH,
        EXPECTED_PREIMAGE_FIXTURE_SHA256,
    )
    source = payload.get("source_catalog") or {}
    if (
        source.get("git_commit") != migration.get("from_catalog_git_commit")
        or source.get("path") != migration.get("from_catalog_path")
        or source.get("byte_sha256") != migration.get("from_catalog_sha256")
        or source.get("program_id") != "loop-product-level-remediation-2026-07-13"
    ):
        raise DispatchError("historical migration catalog source binding mismatch")
    if payload.get("allowed_mutable_live_fields") != EXPECTED_PREIMAGE_MUTABLE_LIVE_FIELDS:
        raise DispatchError("historical migration mutable-field allowlist is not exact")
    return payload


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
    if schema_version != 3:
        raise DispatchError("catalog schema_version must be exactly 3")
    tasks = payload.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise DispatchError("catalog tasks must be a non-empty list")
    if payload.get("task_count") != len(tasks):
        raise DispatchError(
            f"catalog task_count={payload.get('task_count')!r} does not match {len(tasks)}"
        )

    allowed_owner_list = payload.get("allowed_owners")
    if allowed_owner_list != ALLOWED_FLEET_ACTORS:
        raise DispatchError(
            "catalog allowed_owners must be the exact ordered Codex/Codex2 fleet policy"
        )
    allowed_owners = set(allowed_owner_list)
    external = payload.get("external_dependencies")
    if not isinstance(external, list) or len(external) != len(set(external)):
        raise DispatchError("external_dependencies must be a unique list")
    external_ids = {str(item) for item in external}
    if EXPECTED_DISPATCH_PREREQUISITE["task_id"] not in external_ids:
        raise DispatchError("runtime lock bootstrap must be an external dependency")
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
    migration_target_ids: set[str] = set()
    raw_migrations = payload.get("catalog_migrations")
    if isinstance(raw_migrations, list):
        for migration in raw_migrations:
            if isinstance(migration, dict):
                for patch in migration.get("required_live_task_patches") or []:
                    if isinstance(patch, dict) and str(patch.get("task_id") or "").strip():
                        migration_target_ids.add(str(patch["task_id"]))
    contract_marker_ids = additive_ids | migration_target_ids

    if payload.get("execution_task_counts") != {
        "pre_dispatch_bootstrap": 1,
        "primary_catalog": 48,
        "total": 49,
    }:
        raise DispatchError("execution task counts must be exactly 1 bootstrap plus 48 primary")
    if payload.get("dispatch_prerequisite") != EXPECTED_DISPATCH_PREREQUISITE:
        raise DispatchError("runtime/task/audit bootstrap prerequisite is not exact")
    prerequisite_doc = REPO_ROOT / EXPECTED_DISPATCH_PREREQUISITE["task_doc"]
    if not prerequisite_doc.is_file():
        raise DispatchError("runtime lock bootstrap task document is missing")
    prerequisite_fixture = _content_addressed_json(
        REPO_ROOT / EXPECTED_DISPATCH_PREREQUISITE["task_contract_fixture"],
        EXPECTED_DISPATCH_PREREQUISITE["task_contract_fixture_sha256"],
    )
    prerequisite_task = prerequisite_fixture.get("task")
    if (
        prerequisite_fixture.get("schema_version") != 1
        or prerequisite_fixture.get("fixture_set_id")
        != "loop-product-runtime-lock-bootstrap-task-v1"
        or not isinstance(prerequisite_task, dict)
        or prerequisite_task.get("id") != EXPECTED_DISPATCH_PREREQUISITE["task_id"]
        or prerequisite_fixture.get("task_contract_sha256")
        != EXPECTED_DISPATCH_PREREQUISITE["task_contract_sha256"]
        or task_contract_sha256(prerequisite_task)
        != EXPECTED_DISPATCH_PREREQUISITE["task_contract_sha256"]
    ):
        raise DispatchError("runtime lock bootstrap task contract fixture is not exact")
    prerequisite_marker = (
        f"Canonical contract SHA-256: `"
        f"{EXPECTED_DISPATCH_PREREQUISITE['task_contract_sha256']}`"
    )
    if prerequisite_doc.read_text(encoding="utf-8").count(prerequisite_marker) != 1:
        raise DispatchError("runtime lock bootstrap task document contract marker is stale or missing")
    if payload.get("status") != "blocked_on_runtime_lock_bootstrap":
        raise DispatchError("catalog must remain blocked until runtime lock bootstrap is done")
    validate_contract_fixtures(payload)

    execution_authority = payload.get("execution_authority")
    if execution_authority != EXPECTED_EXECUTION_AUTHORITY:
        raise DispatchError(
            "catalog execution_authority must exactly preserve planner, fleet worker, "
            "and distinct fleet reviewer boundaries"
        )
    if payload.get("loop_scope") != EXPECTED_LOOP_SCOPE:
        raise DispatchError(
            "catalog loop_scope must exactly declare twelve canonical L1 loops "
            "and the Per-Persona OODA composite overlay"
        )
    if payload.get("completion_authority") != EXPECTED_COMPLETION_AUTHORITY:
        raise DispatchError("catalog completion_authority must match the exact reviewed contract")
    if payload.get("auth_lifecycle") != EXPECTED_AUTH_LIFECYCLE:
        raise DispatchError("catalog auth_lifecycle must match the exact reviewed contract")
    universal_dispatch_rules = payload.get("universal_dispatch_rules")
    if (
        not isinstance(universal_dispatch_rules, list)
        or not REQUIRED_AUTHORITY_DISPATCH_RULES.issubset(
            set(map(str, universal_dispatch_rules))
        )
    ):
        raise DispatchError(
            "catalog universal_dispatch_rules are missing planner/fleet authority gates"
        )

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
        extra = sorted(set(task) - REQUIRED_TASK_FIELDS)
        if extra:
            raise DispatchError(
                f"{task.get('id', f'tasks[{index}]')} has unbound fields: "
                + ", ".join(extra)
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
        task_loop_ids = {str(item).strip() for item in task["loop_ids"]}
        if "" in task_loop_ids or not task_loop_ids.issubset(REQUIRED_LOOP_IDS):
            unknown = sorted(task_loop_ids - REQUIRED_LOOP_IDS)
            raise DispatchError(
                f"{task_id} loop_ids contains blank or undeclared loops: "
                + ", ".join(unknown or ["<blank>"])
            )
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
        if task_id in contract_marker_ids:
            document = (REPO_ROOT / task_doc).read_text(encoding="utf-8")
            contract_digest = task_contract_sha256(task)
            contract_marker = f"Canonical contract SHA-256: `{contract_digest}`"
            if contract_marker not in document:
                raise DispatchError(
                    f"{task_id} task_doc canonical contract marker is stale or missing"
                )
            if document.count(contract_marker) != 1:
                raise DispatchError(
                    f"{task_id} task_doc canonical contract marker must appear exactly once"
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
        if not isinstance(migrations, list) or len(migrations) != 1:
            raise DispatchError("schema v3 requires exactly one catalog migration")
        migration_ids: set[str] = set()
        patched_task_ids: set[str] = set()
        for migration in migrations:
            if not isinstance(migration, dict):
                raise DispatchError("catalog migration must be an object")
            migration_id = str(migration.get("id") or "").strip()
            if not migration_id or migration_id in migration_ids:
                raise DispatchError("catalog migration IDs must be unique and non-empty")
            if migration_id != "loop-product-gap-addendum-v5":
                raise DispatchError("catalog migration ID must be the reviewed v5 identity")
            migration_ids.add(migration_id)
            from_digest = str(migration.get("from_catalog_sha256") or "")
            if len(from_digest) != 64 or any(
                character not in "0123456789abcdef" for character in from_digest
            ):
                raise DispatchError(f"{migration_id} has invalid from_catalog_sha256")
            fixture = validate_preimage_fixture(migration)
            fixture_targets = {
                str(item.get("task_id")): item
                for item in fixture.get("targets") or []
                if isinstance(item, dict)
            }
            patches = migration.get("required_live_task_patches")
            if not isinstance(patches, list) or len(patches) != 3:
                raise DispatchError(f"{migration_id} must declare exactly three live patches")
            for patch in patches:
                if not isinstance(patch, dict):
                    raise DispatchError(f"{migration_id} task patch must be an object")
                task_id = str(patch.get("task_id") or "").strip()
                if task_id not in by_id or task_id in patched_task_ids:
                    raise DispatchError(
                        f"{migration_id} task patch target is missing or duplicated: {task_id}"
                    )
                patched_task_ids.add(task_id)
                fixture_target = fixture_targets.get(task_id)
                if fixture_target is None:
                    raise DispatchError(f"{migration_id} {task_id} has no exact preimage")
                before = patch.get("before_depends_on")
                appended = patch.get("append_dependencies")
                if (
                    not isinstance(before, list)
                    or not isinstance(appended, list)
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
                allowed_changes = patch.get("allowed_contract_field_changes")
                before_values = fixture_target.get("before_contract_field_values")
                if (
                    not isinstance(allowed_changes, list)
                    or not allowed_changes
                    or len(allowed_changes) != len(set(map(str, allowed_changes)))
                    or set(map(str, allowed_changes)) != set(before_values or {})
                    or allowed_changes
                    != fixture_target.get("allowed_contract_field_changes")
                ):
                    raise DispatchError(
                        f"{migration_id} {task_id} contract change allowlist is not exact"
                    )
                if (
                    patch.get("before_task_contract_sha256")
                    != fixture_target.get("before_task_contract_sha256")
                    or patch.get("after_task_contract_sha256")
                    != fixture_target.get("after_task_contract_sha256")
                    or before != fixture_target.get("before_depends_on")
                    or expected != fixture_target.get("after_depends_on")
                    or patch.get("before_runtime_fields")
                    != fixture_target.get("before_runtime_fields")
                    or patch.get("set_runtime_fields")
                    != fixture_target.get("after_runtime_fields")
                ):
                    raise DispatchError(
                        f"{migration_id} {task_id} patch differs from immutable preimage"
                    )
                current_task = by_id[task_id]
                if task_contract_sha256(current_task) != patch.get(
                    "after_task_contract_sha256"
                ):
                    raise DispatchError(
                        f"{migration_id} {task_id} after-contract digest is stale"
                    )
                reconstructed_before = deepcopy(current_task)
                for field, value in before_values.items():
                    reconstructed_before[str(field)] = deepcopy(value)
                if task_contract_sha256(reconstructed_before) != patch.get(
                    "before_task_contract_sha256"
                ):
                    raise DispatchError(
                        f"{migration_id} {task_id} full historical preimage is not exact"
                    )
        if set(fixture_targets) != patched_task_ids:
            raise DispatchError("catalog migration target set differs from preimage fixture")

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
        if by_id[guard_id]["depends_on"] != authority["guard_direct_dependency_ids"]:
            raise DispatchError("completion guard direct dependency topology changed")
        if by_id[authority_id]["depends_on"] != authority[
            "final_direct_dependency_ids"
        ]:
            raise DispatchError("final authority direct dependency topology changed")
        actual_signoff_ids = [
            task_id
            for task_id, task in by_id.items()
            if task["requires_human_ops_signoff"] is True
        ]
        if actual_signoff_ids != authority["required_human_ops_signoff_task_ids"]:
            raise DispatchError("Human/Ops signoff task-ID authority is not exact")
        for task_id, expected_dependencies in EXPECTED_AUTH_LIFECYCLE[
            "required_direct_dependencies"
        ].items():
            if by_id[task_id]["depends_on"] != expected_dependencies:
                raise DispatchError(
                    f"auth lifecycle direct dependency topology changed for {task_id}"
                )
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
        if set(map(str, by_id[authority_id]["loop_ids"])) != REQUIRED_LOOP_IDS:
            raise DispatchError(
                "completion authority must cover the exact twelve-loop plus OODA union"
            )
        inventory = by_id.get("LOOP-PROD-000")
        if not isinstance(inventory, dict) or set(
            map(str, inventory.get("loop_ids") or [])
        ) != REQUIRED_LOOP_IDS:
            raise DispatchError(
                "LOOP-PROD-000 must inventory the exact twelve-loop plus OODA union"
            )
        checkpoint_ids_with_inventory = {
            authority_id,
            "LOOP-PROD-000",
            *checkpoint_ids,
        }
        uncovered = sorted(
            loop_id
            for loop_id in REQUIRED_LOOP_IDS
            if not any(
                loop_id in set(map(str, task["loop_ids"]))
                and task["target_maturity"] == "product-level"
                and task_id not in checkpoint_ids_with_inventory
                for task_id, task in by_id.items()
            )
        )
        if uncovered:
            raise DispatchError(
                "loops missing a non-close product-level task: "
                + ", ".join(uncovered)
            )
        bindings = authority.get("verdict_binding_fields")
        required_bindings = {
            "program_id",
            "catalog_sha256",
            "task_id",
            "closeout_manifest_sha256",
            "target_environment",
            "frontend_sha",
            "bff_sha",
            "attestation_policy",
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
    active_tasks = state.get("tasks")
    if not isinstance(active_tasks, list):
        raise DispatchError("ai-status.json tasks must be a list")
    active_ids = [
        str(task.get("id") or "").strip()
        for task in active_tasks
        if isinstance(task, dict) and str(task.get("id") or "").strip()
    ]
    duplicate_ids = sorted(
        task_id for task_id, count in Counter(active_ids).items() if count > 1
    )
    if duplicate_ids:
        raise DispatchError(
            "ai-status.json contains duplicate live task IDs: "
            + ", ".join(duplicate_ids)
        )
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
        if dep_id == EXPECTED_DISPATCH_PREREQUISITE["task_id"] and status != "done":
            raise DispatchError(
                "runtime lock bootstrap prerequisite must be exactly done before "
                "authoritative dry-run or apply"
            )


def validate_new_mutation_allowed(state: dict[str, Any]) -> None:
    wave_state = state.get("wave_state")
    if isinstance(wave_state, dict) and wave_state.get("status") == "frozen":
        raise DispatchError(
            "current planning wave is frozen; committed audit recovery may run but "
            "new task mutation is fail-closed"
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


def _runtime_protocol_config() -> dict[str, Any]:
    return {
        "paths": {
            "state_file": str(STATUS_ROOT / ".orchestrator" / "state.json"),
            "event_queue": str(
                STATUS_ROOT / ".orchestrator" / "event-queue.jsonl"
            ),
            "approval_queue": str(
                STATUS_ROOT / ".orchestrator" / "approval-queue.json"
            ),
            "status_file": str(STATUS_PATH),
            "activity_log": str(LOG_PATH),
        },
        "supervisor": {"strict_task_runtime_admission": True},
    }


def _load_runtime_protocol_module(path: Path) -> ModuleType:
    module_name = "loop_product_runtime_lock_protocol"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise DispatchError("runtime lock protocol module cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    orchestrator_path = str(path.parent)
    inserted = orchestrator_path not in sys.path
    if inserted:
        sys.path.insert(0, orchestrator_path)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise DispatchError(
            f"runtime lock protocol module failed to import: {type(exc).__name__}"
        ) from exc
    finally:
        if inserted:
            sys.path.remove(orchestrator_path)
    return module


def _is_lower_hex(value: Any, length: int) -> bool:
    return bool(
        isinstance(value, str)
        and len(value) == length
        and all(character in "0123456789abcdef" for character in value)
    )


def _safe_repo_relative_path(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DispatchError(f"{label} is invalid")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or str(path) != value:
        raise DispatchError(f"{label} must be a normalized repo-relative path")
    return value


def _git_output(root: Path, *args: str) -> bytes:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            check=False,
            capture_output=True,
        )
    except OSError as exc:
        raise DispatchError("runtime lock capability git verification is unavailable") from exc
    if result.returncode != 0:
        raise DispatchError(
            "runtime lock capability git verification failed: " + " ".join(args[:2])
        )
    return result.stdout


def load_runtime_lock_protocol(catalog: dict[str, Any]) -> ModuleType:
    protocol = catalog["dispatch_prerequisite"]["protocol"]
    manifest_path = STATUS_ROOT / protocol["capability_manifest"]
    try:
        manifest_raw = manifest_path.read_bytes()
    except FileNotFoundError as exc:
        raise DispatchError("runtime lock capability manifest is missing") from exc
    except OSError as exc:
        raise DispatchError("runtime lock capability manifest is unreadable") from exc
    manifest = strict_json_loads(manifest_raw, source=str(manifest_path))
    if not isinstance(manifest, dict):
        raise DispatchError("runtime lock capability manifest must be an object")
    manifest_sha256 = sha256_bytes(manifest_raw)
    required_keys = set(protocol["capability_manifest_required_fields"])
    if set(manifest) != required_keys:
        raise DispatchError("runtime lock capability manifest schema is not exact")
    if (
        manifest.get("schema_version") != protocol["schema_version"]
        or manifest.get("protocol_id") != protocol["protocol_id"]
        or manifest.get("module_path") != ".orchestrator/runtime_state.py"
        or manifest.get("lock_order") != protocol["lock_order"]
        or manifest.get("stable_lock_paths") != protocol["stable_lock_paths"]
        or manifest.get("shared_read_supported") is not True
        or manifest.get("api") != protocol["required_api"]
    ):
        raise DispatchError("runtime lock capability manifest contract mismatch")
    merge_sha = manifest.get("merged_commit_sha")
    if not _is_lower_hex(merge_sha, 40):
        raise DispatchError("runtime lock capability merge identity is invalid")
    if (
        manifest.get("bootstrap_task_id")
        != catalog["dispatch_prerequisite"]["task_id"]
        or manifest.get("bootstrap_task_contract_sha256")
        != catalog["dispatch_prerequisite"]["task_contract_sha256"]
    ):
        raise DispatchError("runtime lock capability bootstrap task binding mismatch")
    writers = manifest.get("writers")
    if not isinstance(writers, dict) or set(writers) != set(
        protocol["required_writer_paths"]
    ):
        raise DispatchError("runtime lock capability writer set is not exact")
    for writer_path, expected_digest in writers.items():
        if (
            not isinstance(writer_path, str)
            or Path(writer_path).is_absolute()
            or ".." in Path(writer_path).parts
            or not _is_lower_hex(expected_digest, 64)
        ):
            raise DispatchError("runtime lock capability writer binding is invalid")
        target = STATUS_ROOT / writer_path
        try:
            actual_digest = sha256_bytes(target.read_bytes())
        except OSError as exc:
            raise DispatchError(
                f"runtime lock capability writer is unreadable: {writer_path}"
            ) from exc
        if actual_digest != expected_digest:
            raise DispatchError(
                f"runtime lock capability writer digest mismatch: {writer_path}"
            )
    dispatcher_path = "scripts/dispatch_loop_product_level_remediation_2026-07-13.py"
    executing_dispatcher_sha256 = sha256_bytes(Path(__file__).read_bytes())
    if (
        manifest.get("dispatcher_sha256") != executing_dispatcher_sha256
        or writers.get(dispatcher_path) != executing_dispatcher_sha256
    ):
        raise DispatchError("runtime lock capability executing dispatcher binding mismatch")

    registry_path = _safe_repo_relative_path(
        manifest.get("writer_registry_path"),
        label="runtime lock writer registry path",
    )
    if registry_path != protocol["writer_registry_path"]:
        raise DispatchError("runtime lock writer registry path mismatch")
    registry_digest = manifest.get("writer_registry_sha256")
    if not _is_lower_hex(registry_digest, 64):
        raise DispatchError("runtime lock writer registry digest is invalid")
    registry = _content_addressed_json(
        STATUS_ROOT / registry_path,
        str(registry_digest),
    )
    if set(registry) != {
        "schema_version",
        "protocol_id",
        "transaction_scope",
        "direct_canonical_writes_forbidden",
        "writers",
    } or registry != {
        "schema_version": 1,
        "protocol_id": protocol["protocol_id"],
        "transaction_scope": "complete_read_validate_mutate_replace",
        "direct_canonical_writes_forbidden": True,
        "writers": writers,
    }:
        raise DispatchError("runtime lock writer registry is not exact")

    evidence_path = _safe_repo_relative_path(
        manifest.get("bootstrap_completion_evidence_path"),
        label="runtime lock bootstrap completion evidence path",
    )
    evidence_digest = manifest.get("bootstrap_completion_evidence_sha256")
    if not _is_lower_hex(evidence_digest, 64):
        raise DispatchError("runtime lock bootstrap completion evidence digest is invalid")
    evidence = _content_addressed_json(
        STATUS_ROOT / evidence_path,
        str(evidence_digest),
    )
    if set(evidence) != {
        "schema_version",
        "task_id",
        "task_contract_sha256",
        "conclusion",
        "worker_runtime_identity",
        "reviewer_runtime_identity",
        "checks_sha256",
        "verdict_id",
        "verifier_capability_sha256",
        "signature_algorithm",
        "key_id",
        "policy_version",
        "signature",
        "revocation_checked_at",
        "ledger_entry_id",
    }:
        raise DispatchError("runtime lock bootstrap completion evidence schema is not exact")
    if (
        evidence.get("schema_version") != 1
        or evidence.get("task_id") != catalog["dispatch_prerequisite"]["task_id"]
        or evidence.get("task_contract_sha256")
        != catalog["dispatch_prerequisite"]["task_contract_sha256"]
        or evidence.get("conclusion") != "passed"
        or evidence.get("worker_runtime_identity") not in ALLOWED_FLEET_ACTORS
        or evidence.get("reviewer_runtime_identity") not in ALLOWED_FLEET_ACTORS
        or evidence.get("worker_runtime_identity")
        == evidence.get("reviewer_runtime_identity")
        or not _is_lower_hex(evidence.get("checks_sha256"), 64)
        or evidence.get("verifier_capability_sha256")
        != writers.get(str(manifest["module_path"]))
        or evidence.get("signature_algorithm") != "ed25519"
        or any(
            not isinstance(evidence.get(field), str)
            or not str(evidence[field]).strip()
            for field in (
                "verdict_id",
                "key_id",
                "policy_version",
                "signature",
                "revocation_checked_at",
                "ledger_entry_id",
            )
        )
    ):
        raise DispatchError("runtime lock bootstrap completion evidence is not exact")
    parse_activity_timestamp(evidence["revocation_checked_at"])

    repository_root = Path(
        _git_output(STATUS_ROOT, "rev-parse", "--show-toplevel")
        .decode("utf-8")
        .strip()
    ).resolve()
    if repository_root != STATUS_ROOT:
        raise DispatchError("runtime lock capability repository root is not exact")
    _git_output(STATUS_ROOT, "cat-file", "-e", f"{merge_sha}^{{commit}}")
    _git_output(
        STATUS_ROOT,
        "merge-base",
        "--is-ancestor",
        str(merge_sha),
        "refs/remotes/origin/dev",
    )
    committed_bindings = {**writers, registry_path: registry_digest, evidence_path: evidence_digest}
    for relative_path, expected_digest in committed_bindings.items():
        committed_bytes = _git_output(
            STATUS_ROOT,
            "show",
            f"{merge_sha}:{relative_path}",
        )
        if sha256_bytes(committed_bytes) != expected_digest:
            raise DispatchError(
                f"runtime lock capability merged blob mismatch: {relative_path}"
            )

    module_path = STATUS_ROOT / str(manifest["module_path"])
    module = _load_runtime_protocol_module(module_path)
    if (
        getattr(module, "RUNTIME_TASK_AUDIT_LOCK_PROTOCOL_VERSION", None) != 1
        or getattr(module, "RUNTIME_TASK_AUDIT_LOCK_PROTOCOL_ID", None)
        != protocol["protocol_id"]
    ):
        raise DispatchError("runtime lock protocol module version or identity mismatch")
    for api_name in protocol["required_api"]:
        if not callable(getattr(module, api_name, None)):
            raise DispatchError(f"runtime lock protocol API is missing: {api_name}")
    try:
        verification = module.verify_runtime_lock_capability(
            manifest=deepcopy(manifest),
            manifest_sha256=manifest_sha256,
            writer_registry=deepcopy(registry),
            completion_evidence=deepcopy(evidence),
            repository_root=str(STATUS_ROOT),
        )
    except Exception as exc:
        raise DispatchError(
            "runtime lock protected capability verifier failed: "
            f"{type(exc).__name__}"
        ) from exc
    expected_verification = {
        "schema_version": 1,
        "protocol_id": protocol["protocol_id"],
        "allowed": True,
        "reason_id": "verified",
        "manifest_sha256": manifest_sha256,
        "writer_registry_sha256": str(registry_digest),
        "completion_evidence_sha256": str(evidence_digest),
        "merged_commit_sha": str(merge_sha),
    }
    if verification != expected_verification:
        raise DispatchError(
            "runtime lock protected capability verifier decision is not exact"
        )
    try:
        if sha256_bytes(manifest_path.read_bytes()) != manifest_sha256:
            raise DispatchError("runtime lock capability manifest changed during validation")
        if sha256_bytes((STATUS_ROOT / registry_path).read_bytes()) != registry_digest:
            raise DispatchError("runtime lock writer registry changed during validation")
        if sha256_bytes((STATUS_ROOT / evidence_path).read_bytes()) != evidence_digest:
            raise DispatchError(
                "runtime lock bootstrap completion evidence changed during validation"
            )
        for writer_path, expected_digest in writers.items():
            if sha256_bytes((STATUS_ROOT / writer_path).read_bytes()) != expected_digest:
                raise DispatchError(
                    f"runtime lock capability writer changed during validation: {writer_path}"
                )
    except OSError as exc:
        raise DispatchError(
            "runtime lock capability binding became unreadable during validation"
        ) from exc
    return module


@contextmanager
def shared_dispatch_locks(
    module: ModuleType,
    catalog: dict[str, Any],
    *,
    shared: bool,
) -> Iterator[dict[str, Any]]:
    task_ids = [str(task["id"]) for task in catalog["tasks"]]
    config = _runtime_protocol_config()
    try:
        with module.tasks_runtime_admission_guard(
            config,
            task_ids,
            strict=True,
            shared=shared,
            nonblocking=True,
        ) as admission:
            if not isinstance(admission, dict):
                raise DispatchError("runtime admission guard returned no exact decision")
            protocol = catalog["dispatch_prerequisite"]["protocol"]
            decision_contract = protocol["admission_decision_contract"]
            required_fields = set(decision_contract["required_fields"])
            source_ids = decision_contract["source_ids"]
            source_sha256 = admission.get("source_sha256")
            conflicts = admission.get("conflicts")
            exact_shape = bool(
                set(admission) == required_fields
                and admission.get("schema_version")
                == decision_contract["schema_version"]
                and admission.get("protocol_id") == protocol["protocol_id"]
                and admission.get("strict") is True
                and admission.get("lock_mode")
                == ("shared" if shared else "exclusive")
                and admission.get("task_ids") == task_ids
                and isinstance(source_sha256, dict)
                and list(source_sha256) == source_ids
                and all(_is_lower_hex(value, 64) for value in source_sha256.values())
                and isinstance(conflicts, list)
                and all(isinstance(conflict, dict) for conflict in conflicts)
                and len(conflicts) == len(
                    {canonical_json_sha256(conflict) for conflict in conflicts}
                )
                and _is_lower_hex(admission.get("snapshot_sha256"), 64)
                and admission.get("snapshot_sha256")
                == canonical_json_sha256(source_sha256)
                and isinstance(admission.get("allowed"), bool)
                and isinstance(admission.get("reason_id"), str)
                and bool(str(admission.get("reason_id")).strip())
            )
            if not exact_shape:
                raise DispatchError("runtime admission decision schema is not exact")
            if (
                admission.get("allowed") is not True
                or admission.get("reason_id")
                != decision_contract["clear_reason_id"]
                or conflicts != []
            ):
                reason = admission.get("reason_id") or "runtime_admission_not_exact"
                raise DispatchError(f"runtime admission blocked: {reason}")
            with module.canonical_task_state_lock_file(
                STATUS_PATH,
                shared=shared,
                nonblocking=True,
            ):
                with module.activity_audit_lock_file(
                    LOG_PATH,
                    shared=shared,
                    nonblocking=True,
                ):
                    yield admission
    except DispatchError:
        raise
    except (BlockingIOError, TimeoutError) as exc:
        raise DispatchError("runtime/task/audit lock set is busy") from exc
    except TypeError as exc:
        raise DispatchError("runtime lock protocol API signature mismatch") from exc
    except Exception as exc:
        raise DispatchError(
            f"runtime/task/audit lock acquisition failed: {type(exc).__name__}"
        ) from exc


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


def activity_log_sources(*, since: datetime | None = None) -> list[Path]:
    """Return rotated history followed by the active append-only log.

    Both rotation implementations currently used by Pantheon retain an exact
    tail in the next file.  Exact copies across different source files are
    therefore valid, while duplicates within one source or conflicting uses of
    one event ID are corruption and must stop recovery.
    """

    archives = sorted(
        (STATUS_ROOT / "archive" / "logs").glob("ai-activity-log.jsonl-*.gz")
    )
    archives.extend(
        sorted(
            (STATUS_ROOT / ".orchestrator" / "logs" / "activity-log-archive").glob(
                "ai-activity-log-*.jsonl.gz"
            )
        )
    )
    if since is not None:
        # A pending event cannot have been rotated into an archive created
        # before its own bound timestamp.  Use a small filesystem timestamp
        # grace instead of re-reading years of unrelated compressed history.
        cutoff = since.timestamp() - 300
        archives = [path for path in archives if path.stat().st_mtime >= cutoff]
    sources = archives
    if LOG_PATH.is_file():
        sources.append(LOG_PATH)
    return sources


def _read_activity_source(path: Path) -> str:
    try:
        if path.suffix == ".gz":
            with gzip.open(path, "rt", encoding="utf-8", errors="strict") as handle:
                return handle.read()
        return path.read_text(encoding="utf-8", errors="strict")
    except (OSError, UnicodeError) as exc:
        raise DispatchError(
            f"activity audit source is unreadable: {path}: {type(exc).__name__}"
        ) from exc


def activity_event_index(*, since: datetime | None = None) -> dict[str, str]:
    """Index content-addressed audit events by ID and canonical payload digest."""

    event_payloads: dict[str, str] = {}
    for path in activity_log_sources(since=since):
        source_ids: set[str] = set()
        for line_number, line in enumerate(_read_activity_source(path).splitlines(), 1):
            if not line.strip():
                continue
            entry = strict_json_loads(
                line,
                source=f"activity audit {path}:{line_number}",
            )
            if not isinstance(entry, dict):
                raise DispatchError(
                    f"activity audit entry must be an object in {path}:{line_number}"
                )
            if "event_id" not in entry:
                continue
            event_id = entry.get("event_id")
            if not isinstance(event_id, str) or not event_id.strip():
                raise DispatchError(
                    f"activity audit event_id is invalid in {path}:{line_number}"
                )
            if event_id.startswith("loop-product-event-"):
                event_payload = {
                    key: deepcopy(value)
                    for key, value in entry.items()
                    if key != "event_id"
                }
                expected_event_id = (
                    "loop-product-event-" + canonical_json_sha256(event_payload)
                )
                if event_id != expected_event_id:
                    raise DispatchError(
                        "activity audit event_id payload binding mismatch in "
                        f"{path}:{line_number}"
                    )
            if event_id in source_ids:
                raise DispatchError(
                    f"duplicate activity audit event_id {event_id} in {path}"
                )
            source_ids.add(event_id)
            payload_digest = canonical_json_sha256(entry)
            previous_digest = event_payloads.get(event_id)
            if previous_digest is not None and previous_digest != payload_digest:
                raise DispatchError(
                    f"conflicting activity audit event_id {event_id} across rotated logs"
                )
            event_payloads[event_id] = payload_digest
    return event_payloads


def preflight_activity_events(
    pending: dict[str, Any],
    existing_events: dict[str, str],
) -> list[dict[str, Any]]:
    """Return missing events only after every existing ID is an exact payload match."""

    missing: list[dict[str, Any]] = []
    for entry in pending["events"]:
        event_id = str(entry["event_id"])
        expected_digest = canonical_json_sha256(entry)
        existing_digest = existing_events.get(event_id)
        if existing_digest is None:
            missing.append(entry)
        elif existing_digest != expected_digest:
            raise DispatchError(
                f"activity audit event_id {event_id} has a conflicting payload"
            )
    return missing


def parse_activity_timestamp(raw: Any) -> datetime:
    if not isinstance(raw, str) or not raw.endswith("Z"):
        raise DispatchError("activity timestamp must be an exact UTC ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(raw.removesuffix("Z") + "+00:00")
    except ValueError as exc:
        raise DispatchError("activity timestamp is malformed") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise DispatchError("activity timestamp must be UTC")
    return parsed


def append_logs(entries: list[dict[str, Any]]) -> None:
    if not entries:
        return
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() > 0:
            handle.seek(-1, os.SEEK_END)
            if handle.read(1) != b"\n":
                handle.seek(0, os.SEEK_END)
                handle.write(b"\n")
        handle.seek(0, os.SEEK_END)
        for entry in entries:
            handle.write(
                (json.dumps(entry, ensure_ascii=False) + "\n").encode("utf-8")
            )
        handle.flush()
        os.fsync(handle.fileno())


def build_affected_state_projection(
    state: dict[str, Any],
    catalog: dict[str, Any],
    raw_events: list[dict[str, Any]],
) -> dict[str, Any]:
    active_tasks = state.get("tasks")
    if not isinstance(active_tasks, list):
        raise DispatchError("affected-state projection requires an exact task list")
    task_ids = [
        str(task.get("id") or "")
        for task in active_tasks
        if isinstance(task, dict)
    ]
    if not all(task_ids) or len(task_ids) != len(set(task_ids)):
        raise DispatchError("affected-state projection task identities are not exact")
    active_by_id = {
        str(task["id"]): task for task in active_tasks if isinstance(task, dict)
    }
    records = state.get("program_catalog_migrations") or []
    if not isinstance(records, list) or any(not isinstance(record, dict) for record in records):
        raise DispatchError("affected-state projection migration records are invalid")
    records_by_id = {
        str(record.get("id") or ""): record for record in records
    }
    if "" in records_by_id or len(records_by_id) != len(records):
        raise DispatchError("affected-state projection migration IDs are not exact")
    overlay_key = str(catalog["completion_authority"]["live_overlay_state_key"])
    overlays = state.get(overlay_key) or {}
    if not isinstance(overlays, dict):
        raise DispatchError("affected-state projection completion overlay is invalid")
    program_id = str(catalog["program_id"])
    items: list[dict[str, Any]] = []
    for event in raw_events:
        event_type = str(event["type"])
        task_id = str(event["task_id"])
        if event_type == "assign":
            task = active_by_id.get(task_id)
            if not isinstance(task, dict):
                raise DispatchError(f"affected assignment task is missing: {task_id}")
            items.append(
                {
                    "event_type": event_type,
                    "task_id": task_id,
                    "task": deepcopy(task),
                }
            )
        elif event_type == "catalog_migration":
            task = active_by_id.get(task_id)
            record = records_by_id.get(str(event["migration_id"]))
            if not isinstance(task, dict) or not isinstance(record, dict):
                raise DispatchError(
                    f"affected migration task or record is missing: {task_id}"
                )
            items.append(
                {
                    "event_type": event_type,
                    "task_id": task_id,
                    "task": deepcopy(task),
                    "migration_record": deepcopy(record),
                }
            )
        elif event_type == "completion_authority_install":
            overlay = overlays.get(program_id)
            if not isinstance(overlay, dict):
                raise DispatchError("affected completion overlay is missing")
            items.append(
                {
                    "event_type": event_type,
                    "task_id": task_id,
                    "completion_overlay": deepcopy(overlay),
                }
            )
        else:
            raise DispatchError(f"unsupported affected-state event: {event_type}")
    return {
        "schema_version": 1,
        "program_id": program_id,
        "items": items,
    }


def validate_affected_state_projection(
    state: dict[str, Any],
    catalog: dict[str, Any],
    raw_events: list[dict[str, Any]],
    projection: Any,
    *,
    require_exact_current: bool,
) -> None:
    program_id = str(catalog["program_id"])
    if (
        not isinstance(projection, dict)
        or set(projection) != {"schema_version", "program_id", "items"}
        or projection.get("schema_version") != 1
        or projection.get("program_id") != program_id
        or not isinstance(projection.get("items"), list)
        or len(projection["items"]) != len(raw_events)
    ):
        raise DispatchError("program_activity_outbox affected-state schema is not exact")
    active_tasks = state.get("tasks")
    if not isinstance(active_tasks, list) or any(
        not isinstance(task, dict) for task in active_tasks
    ):
        raise DispatchError("program_activity_outbox current task state is invalid")
    active_ids = [str(task.get("id") or "") for task in active_tasks]
    if not all(active_ids) or len(active_ids) != len(set(active_ids)):
        raise DispatchError("program_activity_outbox current task IDs are not exact")
    active_by_id = {str(task["id"]): task for task in active_tasks}
    records = state.get("program_catalog_migrations") or []
    if not isinstance(records, list) or any(not isinstance(record, dict) for record in records):
        raise DispatchError("program_activity_outbox current migration state is invalid")
    record_by_id = {str(record.get("id") or ""): record for record in records}
    overlay_key = str(catalog["completion_authority"]["live_overlay_state_key"])
    overlays = state.get(overlay_key) or {}
    if not isinstance(overlays, dict):
        raise DispatchError("program_activity_outbox current overlay state is invalid")
    for event, item in zip(raw_events, projection["items"], strict=True):
        event_type = str(event["type"])
        task_id = str(event["task_id"])
        common = {"event_type", "task_id"}
        expected_fields = (
            common | {"task"}
            if event_type == "assign"
            else common | {"task", "migration_record"}
            if event_type == "catalog_migration"
            else common | {"completion_overlay"}
        )
        if (
            not isinstance(item, dict)
            or set(item) != expected_fields
            or item.get("event_type") != event_type
            or item.get("task_id") != task_id
        ):
            raise DispatchError("program_activity_outbox affected-state item is not exact")
        if event_type in {"assign", "catalog_migration"}:
            snapshot_task = item.get("task")
            current_task = active_by_id.get(task_id)
            if (
                not isinstance(snapshot_task, dict)
                or snapshot_task.get("id") != task_id
                or not isinstance(current_task, dict)
            ):
                raise DispatchError(
                    f"program_activity_outbox affected task is missing: {task_id}"
                )
            if event_type == "assign":
                if (
                    snapshot_task.get("owner") != event.get("assigned_owner")
                    or snapshot_task.get("reviewer") != event.get("assigned_reviewer")
                    or snapshot_task.get("created_at") != event.get("created_at")
                    or task_contract_sha256(snapshot_task)
                    != event.get("task_contract_sha256")
                    or canonical_json_sha256(snapshot_task.get("source_ref"))
                    != event.get("source_ref_sha256")
                    or task_contract_sha256(current_task)
                    != event.get("task_contract_sha256")
                    or canonical_json_sha256(current_task.get("source_ref"))
                    != event.get("source_ref_sha256")
                ):
                    raise DispatchError(
                        f"program_activity_outbox assignment state drift: {task_id}"
                    )
            else:
                record = item.get("migration_record")
                current_record = record_by_id.get(str(event.get("migration_id")))
                patches = record.get("patches") if isinstance(record, dict) else None
                matching_patch = next(
                    (
                        patch
                        for patch in patches or []
                        if isinstance(patch, dict) and patch.get("task_id") == task_id
                    ),
                    None,
                )
                if (
                    not isinstance(record, dict)
                    or record.get("id") != event.get("migration_id")
                    or canonical_json_sha256(record)
                    != event.get("migration_record_sha256")
                    or current_record != record
                    or not isinstance(matching_patch, dict)
                    or matching_patch.get("before_task_contract_sha256")
                    != event.get("before_task_contract_sha256")
                    or matching_patch.get("after_task_contract_sha256")
                    != event.get("after_task_contract_sha256")
                    or task_contract_sha256(snapshot_task)
                    != event.get("after_task_contract_sha256")
                    or task_contract_sha256(current_task)
                    != event.get("after_task_contract_sha256")
                ):
                    raise DispatchError(
                        f"program_activity_outbox migration state drift: {task_id}"
                    )
            if require_exact_current and current_task != snapshot_task:
                raise DispatchError(
                    f"program_activity_outbox proposed task snapshot changed: {task_id}"
                )
        else:
            overlay = item.get("completion_overlay")
            current_overlay = overlays.get(program_id)
            if (
                not isinstance(overlay, dict)
                or canonical_json_sha256(overlay)
                != event.get("completion_overlay_sha256")
                or overlay.get("completion_authority_sha256")
                != event.get("completion_authority_sha256")
                or current_overlay != overlay
            ):
                raise DispatchError("program_activity_outbox completion overlay drift")


def enqueue_activity_outbox(
    state: dict[str, Any],
    entries: list[dict[str, Any]],
    *,
    catalog: dict[str, Any],
    catalog_digest: str,
) -> None:
    pending = state.get("program_activity_outbox")
    if pending not in (None, {}, []):
        raise DispatchError("program_activity_outbox must be recovered before enqueue")
    if not entries:
        raise DispatchError("cannot enqueue an empty activity transaction")
    actor = str(os.environ.get("AI_NAME") or "").strip()
    program_id = str(catalog.get("program_id") or "").strip()
    current_actor_policy = catalog.get("allowed_owners")
    if (
        not program_id
        or not isinstance(current_actor_policy, list)
        or not current_actor_policy
        or any(
            not isinstance(item, str) or not item.strip()
            for item in current_actor_policy
        )
        or len(current_actor_policy) != len(set(current_actor_policy))
    ):
        raise DispatchError("current catalog actor policy is not exact")
    actor_policy = deepcopy(current_actor_policy)
    if actor not in actor_policy:
        raise DispatchError("AI_NAME must name an allowed fleet actor before any write")

    raw_events: list[dict[str, Any]] = []
    for raw_entry in entries:
        if not isinstance(raw_entry, dict):
            raise DispatchError("activity event must be an object")
        event_type = str(raw_entry.get("type") or "")
        expected_fields = {
            "ts",
            "agent",
            "type",
            "task_id",
            "message",
        } | ACTIVITY_EVENT_EXTRA_FIELDS.get(event_type, set())
        if set(raw_entry) != expected_fields:
            raise DispatchError(f"{event_type or 'activity'} event schema is incomplete")
        if event_type not in ACTIVITY_EVENT_TYPES:
            raise DispatchError(f"unsupported activity event type: {event_type!r}")
        if raw_entry.get("agent") != actor:
            raise DispatchError("activity event actor differs from the admitted AI_NAME")
        for field in {"ts", "agent", "type", "task_id", "message"}:
            if not isinstance(raw_entry.get(field), str) or not str(
                raw_entry.get(field)
            ).strip():
                raise DispatchError("activity event core strings must be non-empty")
        parse_activity_timestamp(raw_entry["ts"])
        raw_events.append(deepcopy(raw_entry))

    created_at = min(str(entry["ts"]) for entry in raw_events)
    actor_policy_sha256 = canonical_json_sha256(actor_policy)
    affected_state_projection = build_affected_state_projection(
        state,
        catalog,
        raw_events,
    )
    affected_state_projection_sha256 = canonical_json_sha256(
        affected_state_projection
    )
    transaction_seed = {
        "schema_version": ACTIVITY_OUTBOX_SCHEMA_VERSION,
        "program_id": program_id,
        "catalog_sha256": catalog_digest,
        "actor": actor,
        "actor_policy": actor_policy,
        "actor_policy_sha256": actor_policy_sha256,
        "created_at": created_at,
        "affected_state_projection": affected_state_projection,
        "affected_state_projection_sha256": affected_state_projection_sha256,
        "raw_events": raw_events,
    }
    transaction_id = "loop-product-tx-" + canonical_json_sha256(transaction_seed)
    prepared: list[dict[str, Any]] = []
    for index, raw_event in enumerate(raw_events):
        event = {
            **raw_event,
            "ordinal": index,
            "event_count": len(raw_events),
            "program_id": program_id,
            "catalog_sha256": catalog_digest,
            "transaction_id": transaction_id,
            "actor_policy_sha256": actor_policy_sha256,
            "affected_state_projection_sha256": (
                affected_state_projection_sha256
            ),
        }
        event["event_id"] = "loop-product-event-" + canonical_json_sha256(event)
        prepared.append(event)
    state["program_activity_outbox"] = {
        "schema_version": ACTIVITY_OUTBOX_SCHEMA_VERSION,
        "transaction_id": transaction_id,
        "program_id": program_id,
        "catalog_sha256": catalog_digest,
        "actor": actor,
        "actor_policy": actor_policy,
        "actor_policy_sha256": actor_policy_sha256,
        "created_at": created_at,
        "affected_state_projection": affected_state_projection,
        "affected_state_projection_sha256": affected_state_projection_sha256,
        "events": prepared,
    }


def validate_activity_outbox(
    state: dict[str, Any],
    catalog: dict[str, Any],
    catalog_digest: str,
    *,
    require_current_catalog: bool,
) -> dict[str, Any]:
    pending = state.get("program_activity_outbox")
    if not isinstance(pending, dict) or set(pending) != ACTIVITY_TRANSACTION_FIELDS:
        raise DispatchError("program_activity_outbox transaction schema is not exact")
    if pending.get("schema_version") != ACTIVITY_OUTBOX_SCHEMA_VERSION:
        raise DispatchError("program_activity_outbox schema version mismatch")
    expected_program = str(catalog["program_id"])
    if pending.get("program_id") != expected_program:
        raise DispatchError("program_activity_outbox program binding mismatch")
    bound_catalog = pending.get("catalog_sha256")
    if (
        not isinstance(bound_catalog, str)
        or len(bound_catalog) != 64
        or (require_current_catalog and bound_catalog != catalog_digest)
    ):
        raise DispatchError("program_activity_outbox catalog binding mismatch")
    actor_policy = pending.get("actor_policy")
    if (
        not isinstance(actor_policy, list)
        or not actor_policy
        or any(
            not isinstance(item, str) or not item.strip() for item in actor_policy
        )
        or len(actor_policy) != len(set(actor_policy))
        or pending.get("actor") not in actor_policy
        or pending.get("actor_policy_sha256") != canonical_json_sha256(actor_policy)
        or (
            require_current_catalog
            and actor_policy != catalog.get("allowed_owners")
        )
    ):
        raise DispatchError("program_activity_outbox actor policy binding mismatch")
    parse_activity_timestamp(pending.get("created_at"))
    events = pending.get("events")
    if not isinstance(events, list) or not events or any(
        not isinstance(item, dict) for item in events
    ):
        raise DispatchError("program_activity_outbox events must be non-empty objects")
    raw_events: list[dict[str, Any]] = []
    event_ids: set[str] = set()
    for expected_ordinal, entry in enumerate(events):
        event_type = str(entry.get("type") or "")
        extra_fields = ACTIVITY_EVENT_EXTRA_FIELDS.get(event_type)
        if extra_fields is None or set(entry) != ACTIVITY_COMMON_EVENT_FIELDS | extra_fields:
            raise DispatchError("program_activity_outbox event schema is not exact")
        if entry.get("program_id") != expected_program:
            raise DispatchError("program_activity_outbox program binding mismatch")
        if entry.get("catalog_sha256") != bound_catalog:
            raise DispatchError("program_activity_outbox catalog binding mismatch")
        if entry.get("ordinal") != expected_ordinal:
            raise DispatchError("program_activity_outbox ordinal sequence is invalid")
        if entry.get("event_count") != len(events):
            raise DispatchError("program_activity_outbox event count mismatch")
        transaction_id = entry.get("transaction_id")
        event_id = entry.get("event_id")
        if transaction_id != pending.get("transaction_id"):
            raise DispatchError("program_activity_outbox transaction binding mismatch")
        if entry.get("actor_policy_sha256") != pending.get("actor_policy_sha256"):
            raise DispatchError("program_activity_outbox actor policy event mismatch")
        if entry.get("affected_state_projection_sha256") != pending.get(
            "affected_state_projection_sha256"
        ):
            raise DispatchError(
                "program_activity_outbox affected-state event binding mismatch"
            )
        if not isinstance(event_id, str) or not event_id.strip() or event_id in event_ids:
            raise DispatchError("program_activity_outbox event_id is missing or duplicated")
        if any(not isinstance(entry.get(field), str) or not str(entry.get(field)).strip()
               for field in {"ts", "agent", "type", "task_id", "message"}):
            raise DispatchError("program_activity_outbox core strings must be non-empty")
        parse_activity_timestamp(entry["ts"])
        if entry["agent"] != pending.get("actor"):
            raise DispatchError("program_activity_outbox agent binding is invalid")
        if event_type == "assign":
            expected_message = (
                f"Assigned {entry['task_id']} to {entry['assigned_owner']} "
                f"with reviewer {entry['assigned_reviewer']} from {expected_program}"
            )
            if (
                entry.get("ts") != entry.get("created_at")
                or entry.get("message") != expected_message
            ):
                raise DispatchError("assignment activity is not bound to its live task")
            for digest_field in ("task_contract_sha256", "source_ref_sha256"):
                if len(str(entry.get(digest_field) or "")) != 64:
                    raise DispatchError("assignment immutable digest binding is invalid")
            if entry.get("created_at") != entry.get("ts"):
                raise DispatchError("assignment created-at binding mismatch")
        elif event_type == "catalog_migration":
            expected_message = (
                f"Applied {entry['migration_id']} exact catalog migration to "
                f"{entry['task_id']}"
            )
            if entry.get("message") != expected_message or any(
                len(str(entry.get(field) or "")) != 64
                for field in (
                    "migration_record_sha256",
                    "before_task_contract_sha256",
                    "after_task_contract_sha256",
                )
            ):
                raise DispatchError("migration immutable event binding is invalid")
        else:
            if entry.get("message") != (
                "Installed exact catalog-bound completion authority overlay"
            ) or any(
                len(str(entry.get(field) or "")) != 64
                for field in (
                    "completion_authority_sha256",
                    "completion_overlay_sha256",
                )
            ):
                raise DispatchError("completion overlay event binding is invalid")

        raw_event = {
            key: deepcopy(value)
            for key, value in entry.items()
            if key not in ACTIVITY_COMMON_EVENT_FIELDS
            or key in {"ts", "agent", "type", "task_id", "message"}
        }
        raw_events.append(raw_event)
        event_ids.add(event_id)
        expected_event = {key: deepcopy(value) for key, value in entry.items() if key != "event_id"}
        expected_event_id = "loop-product-event-" + canonical_json_sha256(expected_event)
        if event_id != expected_event_id:
            raise DispatchError("program_activity_outbox event_id binding mismatch")
    affected_state_projection = pending.get("affected_state_projection")
    if pending.get("affected_state_projection_sha256") != canonical_json_sha256(
        affected_state_projection
    ):
        raise DispatchError("program_activity_outbox affected-state binding mismatch")
    validate_affected_state_projection(
        state,
        catalog,
        raw_events,
        affected_state_projection,
        require_exact_current=require_current_catalog,
    )
    transaction_seed = {
        "schema_version": ACTIVITY_OUTBOX_SCHEMA_VERSION,
        "program_id": expected_program,
        "catalog_sha256": bound_catalog,
        "actor": pending["actor"],
        "actor_policy": actor_policy,
        "actor_policy_sha256": pending["actor_policy_sha256"],
        "created_at": pending["created_at"],
        "affected_state_projection": affected_state_projection,
        "affected_state_projection_sha256": pending[
            "affected_state_projection_sha256"
        ],
        "raw_events": raw_events,
    }
    expected_transaction_id = "loop-product-tx-" + canonical_json_sha256(
        transaction_seed
    )
    if pending.get("transaction_id") != expected_transaction_id:
        raise DispatchError("program_activity_outbox transaction digest mismatch")
    return pending


def flush_activity_outbox(
    state: dict[str, Any],
    catalog: dict[str, Any],
    catalog_digest: str,
) -> bool:
    pending = state.get("program_activity_outbox")
    if pending in (None, {}, []):
        return False
    pending = validate_activity_outbox(
        state,
        catalog,
        catalog_digest,
        require_current_catalog=False,
    )
    events = pending["events"]
    earliest_event = min(parse_activity_timestamp(entry["ts"]) for entry in events)
    existing = activity_event_index(since=earliest_event)
    missing_events = preflight_activity_events(pending, existing)
    appended_count = 0
    fail_after = str(
        os.environ.get("LOOP_PRODUCT_DISPATCH_FAIL_AFTER_ACTIVITY_EVENT") or ""
    ).strip()
    fail_after_count = int(fail_after) if fail_after.isdigit() else 0
    for entry in missing_events:
        append_logs([entry])
        appended_count += 1
        if fail_after_count and appended_count == fail_after_count:
            raise DispatchError(
                "injected failure during activity append; exact outbox remains pending"
            )
    if os.environ.get("LOOP_PRODUCT_DISPATCH_FAIL_AFTER_ACTIVITY_APPEND") == "1":
        raise DispatchError(
            "injected failure after activity append; status outbox remains pending"
        )
    final_index = activity_event_index(since=earliest_event)
    if preflight_activity_events(pending, final_index):
        raise DispatchError(
            "activity audit transaction is incomplete; exact outbox remains pending"
        )
    current = read_json(STATUS_PATH)
    if current.get("program_activity_outbox") != pending:
        raise DispatchError(
            "status outbox changed before clear; audit appended but no stale status overwrite"
        )
    current["program_activity_outbox"] = None
    atomic_write_json(STATUS_PATH, current)
    state.clear()
    state.update(current)
    return True


def expected_completion_role(task_id: str, catalog: dict[str, Any]) -> str:
    authority = catalog.get("completion_authority") or {}
    authority_id = str(authority.get("task_id") or "")
    checkpoint_ids = {
        str(item) for item in authority.get("checkpoint_only_task_ids") or []
    }
    completion_role = "ordinary"
    if task_id == authority_id:
        completion_role = "final_authority"
    elif task_id == str(authority.get("guard_install_task_id") or ""):
        completion_role = "guard_installer"
    elif task_id in checkpoint_ids:
        completion_role = "checkpoint_only"
    return completion_role


def build_task(
    task: dict[str, Any],
    catalog: dict[str, Any],
    catalog_digest: str,
    timestamp: str,
) -> dict[str, Any]:
    result = deepcopy(task)
    completion_role = expected_completion_role(str(task["id"]), catalog)
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
            "execution_role": catalog["execution_authority"]["implementation_role"],
            "review_role": catalog["execution_authority"]["review_role"],
            "planner_controller_identity": catalog["execution_authority"]
            ["planner_controller_identity"],
            "planner_may_edit_declared_product_artifacts": False,
            "formal_review_required": True,
            "source_ref": {
                "plan": catalog["source_plan"],
                "packet": catalog["packet"],
                "catalog": catalog_ref,
                "catalog_sha256": catalog_digest,
                "task_contract_sha256": task_contract_sha256(task),
                "execution_authority_sha256": execution_authority_sha256(catalog),
                "completion_authority_sha256": completion_authority_sha256(catalog),
                "auth_lifecycle_sha256": auth_lifecycle_sha256(catalog),
                "contract_fixtures_sha256": contract_fixtures_sha256(catalog),
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


def validate_additive_collision(
    existing: dict[str, Any],
    task: dict[str, Any],
    catalog: dict[str, Any],
    catalog_digest: str,
    *,
    source: str,
) -> None:
    """Reject foreign or stale rows that collide with an additive authority ID."""

    task_id = str(task["id"])
    source_ref = existing.get("source_ref")
    expected_contract = task_contract_sha256(task)
    actual_contract = task_contract_sha256(existing)
    expected_metadata = {
        "task_class": "execution",
        "auto_created_by": AUTO_BY,
        "auto_generated": True,
        "delivery_layer": "primary",
        "mutates_canonical": True,
        "helper_kind": "loop_product_level_execution_slice",
        "completion_role": expected_completion_role(task_id, catalog),
        "execution_role": catalog["execution_authority"]["implementation_role"],
        "review_role": catalog["execution_authority"]["review_role"],
        "planner_controller_identity": catalog["execution_authority"]
        ["planner_controller_identity"],
        "planner_may_edit_declared_product_artifacts": False,
        "formal_review_required": True,
    }
    metadata_matches = all(
        existing.get(field) == value for field, value in expected_metadata.items()
    )
    source_matches = bool(
        isinstance(source_ref, dict)
        and source_ref.get("program_id") == catalog.get("program_id")
        and source_ref.get("catalog_sha256") == catalog_digest
        and source_ref.get("task_contract_sha256") == expected_contract
        and source_ref.get("execution_authority_sha256")
        == execution_authority_sha256(catalog)
        and source_ref.get("completion_authority_sha256")
        == completion_authority_sha256(catalog)
        and source_ref.get("auth_lifecycle_sha256") == auth_lifecycle_sha256(catalog)
        and source_ref.get("contract_fixtures_sha256")
        == contract_fixtures_sha256(catalog)
    )
    if not metadata_matches or not source_matches or actual_contract != expected_contract:
        raise DispatchError(
            f"foreign or stale {source} collision for additive task {task_id}; "
            "program, catalog, contract, or completion role is not exact"
        )


def _has_live_admission(task: dict[str, Any]) -> bool:
    return any(
        task.get(field) not in (None, "", 0, False, [], {})
        for field in LIVE_ADMISSION_MARKER_FIELDS
    )


def expected_migration_patches(migration: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for patch in migration["required_live_task_patches"]:
        before = [str(item) for item in patch["before_depends_on"]]
        appended = [str(item) for item in patch["append_dependencies"]]
        after = [*before, *appended]
        result.append(
            {
                "task_id": str(patch["task_id"]),
                "before_task_contract_sha256": str(
                    patch["before_task_contract_sha256"]
                ),
                "after_task_contract_sha256": str(
                    patch["after_task_contract_sha256"]
                ),
                "before_depends_on_sha256": canonical_json_sha256(before),
                "after_depends_on_sha256": canonical_json_sha256(after),
                "appended_dependencies": appended,
                "allowed_contract_field_changes": list(
                    patch["allowed_contract_field_changes"]
                ),
                "before_runtime_fields": deepcopy(patch["before_runtime_fields"]),
                "after_runtime_fields": deepcopy(patch["set_runtime_fields"]),
            }
        )
    return result


def expected_migration_record(
    migration: dict[str, Any],
    catalog: dict[str, Any],
    catalog_digest: str,
    applied_at: str,
) -> dict[str, Any]:
    return {
        "id": str(migration["id"]),
        "program_id": str(catalog["program_id"]),
        "from_catalog_git_commit": str(migration["from_catalog_git_commit"]),
        "from_catalog_path": str(migration["from_catalog_path"]),
        "from_catalog_sha256": str(migration["from_catalog_sha256"]),
        "preimage_fixture": str(migration["preimage_fixture"]),
        "preimage_fixture_sha256": str(migration["preimage_fixture_sha256"]),
        "to_catalog_sha256": catalog_digest,
        "applied_at": applied_at,
        "patches": expected_migration_patches(migration),
    }


def validate_migration_records(
    records: Any,
    catalog: dict[str, Any],
    catalog_digest: str,
) -> dict[str, dict[str, Any]]:
    if not isinstance(records, list):
        raise DispatchError("program_catalog_migrations must be a list")
    record_by_id: dict[str, dict[str, Any]] = {}
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise DispatchError(f"program_catalog_migrations[{index}] must be an object")
        migration_id = record.get("id")
        if not isinstance(migration_id, str) or not migration_id.strip():
            raise DispatchError(f"program_catalog_migrations[{index}] has no valid id")
        if migration_id in record_by_id:
            raise DispatchError(f"duplicate program catalog migration record: {migration_id}")
        record_by_id[migration_id] = record

    known = {str(item["id"]): item for item in catalog.get("catalog_migrations") or []}
    unknown_ids = sorted(set(record_by_id) - set(known))
    if unknown_ids:
        raise DispatchError(
            "unknown program catalog migration records: " + ", ".join(unknown_ids)
        )
    for migration_id, migration in known.items():
        record = record_by_id.get(migration_id)
        if record is None:
            continue
        applied_at = record.get("applied_at")
        if not isinstance(applied_at, str) or not applied_at.strip():
            raise DispatchError(
                f"{migration_id} migration audit record has invalid applied_at"
            )
        expected = expected_migration_record(
            migration,
            catalog,
            catalog_digest,
            applied_at,
        )
        if record != expected:
            raise DispatchError(
                f"{migration_id} migration audit record is not canonical and exact"
            )
    return record_by_id


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
    catalog_by_id = {str(task["id"]): task for task in catalog.get("tasks") or []}
    has_program_tasks = any(task_id in catalog_by_id for task_id in active_by_id)
    records = state.get("program_catalog_migrations")
    if records is None:
        records = []
    record_by_id = validate_migration_records(records, catalog, catalog_digest)

    migrated: list[str] = []
    logs: list[dict[str, Any]] = []
    pending_records: list[dict[str, Any]] = []
    fixture = validate_preimage_fixture(migrations[0])
    historical_source_ref = fixture["historical_source_ref"]
    historical_metadata = fixture["historical_dispatcher_metadata"]

    def runtime_fields_match(task: dict[str, Any], specification: dict[str, Any]) -> bool:
        for field, expected in specification.items():
            if expected == {"presence": "absent"}:
                if field in task:
                    return False
            elif task.get(field) != expected:
                return False
        return True

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
        for patch in patches:
            task_id = str(patch["task_id"])
            existing = active_by_id[task_id]
            if (
                existing.get("owner") not in ALLOWED_FLEET_ACTORS
                or existing.get("reviewer") not in ALLOWED_FLEET_ACTORS
                or existing.get("owner") == existing.get("reviewer")
                or not isinstance(existing.get("next"), str)
                or not existing["next"].strip()
            ):
                raise DispatchError(
                    f"{migration_id} {task_id} mutable fleet assignment is invalid"
                )
            created_at = parse_activity_timestamp(existing.get("created_at"))
            last_update = parse_activity_timestamp(existing.get("last_update"))
            if last_update < created_at:
                raise DispatchError(
                    f"{migration_id} {task_id} mutable timestamps are not monotonic"
                )
            contract_digest = task_contract_sha256(existing)
            if (
                contract_digest == patch["after_task_contract_sha256"]
                and runtime_fields_match(existing, patch["set_runtime_fields"])
            ):
                modes.append("after")
                continue
            if (
                contract_digest != patch["before_task_contract_sha256"]
                or not runtime_fields_match(existing, patch["before_runtime_fields"])
            ):
                raise DispatchError(
                    f"{migration_id} {task_id} full immutable preimage changed; no write performed"
                )
            source_ref = existing.get("source_ref")
            if not isinstance(source_ref, dict):
                raise DispatchError(f"{migration_id} {task_id} has no source_ref")
            metadata_projection = {
                field: existing.get(field)
                for field in historical_metadata["value"]
            }
            if (
                canonical_json_sha256(source_ref) != historical_source_ref["sha256"]
                or source_ref != historical_source_ref["value"]
                or canonical_json_sha256(metadata_projection)
                != historical_metadata["sha256"]
                or metadata_projection != historical_metadata["value"]
            ):
                raise DispatchError(
                    f"{migration_id} {task_id} historical provenance preimage changed"
                )
            if existing.get("status") != "todo" or _has_live_admission(existing):
                raise DispatchError(
                    f"{migration_id} {task_id} is no longer pristine todo; no write performed"
                )
            expected_live_fields = (
                set(REQUIRED_TASK_FIELDS)
                | set(historical_metadata["value"])
                | {"created_at", "last_update", "source_ref"}
            )
            if set(existing) != expected_live_fields:
                raise DispatchError(
                    f"{migration_id} {task_id} historical live-task schema changed"
                )
            modes.append("before")

        if len(set(modes)) != 1:
            raise DispatchError(
                f"{migration_id} is partially applied; refusing an unaudited repair"
            )
        if modes[0] == "after":
            record = record_by_id.get(migration_id)
            if record is None:
                direct_current_catalog = True
                for patch in patches:
                    task_id = str(patch["task_id"])
                    existing = active_by_id[task_id]
                    expected_task = catalog_by_id.get(task_id)
                    source_ref = existing.get("source_ref") or {}
                    expected_contract = (
                        task_contract_sha256(expected_task)
                        if isinstance(expected_task, dict)
                        else ""
                    )
                    direct_current_catalog = direct_current_catalog and bool(
                        expected_task
                        and existing.get("auto_created_by") == AUTO_BY
                        and source_ref.get("program_id") == catalog["program_id"]
                        and source_ref.get("catalog_sha256") == catalog_digest
                        and source_ref.get("task_contract_sha256") == expected_contract
                        and source_ref.get("completion_authority_sha256")
                        == completion_authority_sha256(catalog)
                        and source_ref.get("auth_lifecycle_sha256")
                        == auth_lifecycle_sha256(catalog)
                        and source_ref.get("contract_fixtures_sha256")
                        == contract_fixtures_sha256(catalog)
                        and task_contract_sha256(existing) == expected_contract
                    )
                if not direct_current_catalog:
                    raise DispatchError(
                        f"{migration_id} dependencies changed without an exact audit record"
                    )
            continue

        if migration_id in record_by_id:
            raise DispatchError(
                f"{migration_id} audit record exists while live tasks remain at the preimage"
            )

        for patch in patches:
            task_id = str(patch["task_id"])
            expected_task = catalog_by_id[task_id]
            for field in TASK_CONTRACT_FIELDS:
                active_by_id[task_id][field] = deepcopy(expected_task.get(field))
            for field, value in patch["set_runtime_fields"].items():
                active_by_id[task_id][field] = deepcopy(value)
            migrated.append(task_id)
        record = expected_migration_record(
            migration,
            catalog,
            catalog_digest,
            timestamp,
        )
        record_digest = canonical_json_sha256(record)
        pending_records.append(record)
        for patch in patches:
            task_id = str(patch["task_id"])
            logs.append(
                {
                    "ts": timestamp,
                    "agent": str(os.environ.get("AI_NAME") or ""),
                    "type": "catalog_migration",
                    "task_id": task_id,
                    "migration_id": migration_id,
                    "migration_record_sha256": record_digest,
                    "before_task_contract_sha256": str(
                        patch["before_task_contract_sha256"]
                    ),
                    "after_task_contract_sha256": str(
                        patch["after_task_contract_sha256"]
                    ),
                    "message": (
                        f"Applied {migration_id} exact catalog migration to {task_id}"
                    ),
                }
            )

    if not migrated:
        return [], [], False
    state["program_catalog_migrations"] = [*records, *pending_records]
    state["updated_at"] = timestamp
    return migrated, logs, True


def expected_completion_overlay(
    catalog: dict[str, Any],
    catalog_digest: str,
) -> dict[str, Any]:
    roles = deepcopy(catalog["completion_authority"]["live_overlay_roles"])
    catalog_by_id = {str(task["id"]): task for task in catalog["tasks"]}
    return {
        "schema_version": 1,
        "program_id": str(catalog["program_id"]),
        "catalog_sha256": catalog_digest,
        "completion_authority_sha256": completion_authority_sha256(catalog),
        "roles": roles,
        "required_human_ops_signoff_task_ids": list(
            catalog["completion_authority"]["required_human_ops_signoff_task_ids"]
        ),
        "task_contract_sha256_by_role": {
            task_id: task_contract_sha256(catalog_by_id[task_id])
            for task_id in roles
        },
        "contract_fixtures_sha256": contract_fixtures_sha256(catalog),
        "checkpoint_consumption_required": True,
        "checkpoint_consumption_record_contract_sha256": canonical_json_sha256(
            catalog["completion_authority"]["checkpoint_consumption_record_contract"]
        ),
    }


def ensure_completion_overlay(
    state: dict[str, Any],
    catalog: dict[str, Any],
    catalog_digest: str,
    timestamp: str,
) -> tuple[list[dict[str, Any]], bool]:
    key = str(catalog["completion_authority"]["live_overlay_state_key"])
    overlays = state.get(key)
    if overlays is None:
        overlays = {}
    if not isinstance(overlays, dict):
        raise DispatchError("program completion authority overlays must be an object")
    program_id = str(catalog["program_id"])
    expected = expected_completion_overlay(catalog, catalog_digest)
    existing = overlays.get(program_id)
    if existing is not None:
        if existing != expected:
            raise DispatchError("foreign or tampered program completion overlay")
        return [], False

    active_by_id = {
        str(task.get("id")): task
        for task in state.get("tasks") or []
        if isinstance(task, dict) and str(task.get("id") or "").strip()
    }
    for task_id, role in expected["roles"].items():
        task = active_by_id.get(task_id)
        if task is not None and task.get("completion_role") not in (None, role):
            raise DispatchError(
                f"task-local completion role conflicts with overlay: {task_id}"
            )
    overlays[program_id] = expected
    state[key] = overlays
    state["updated_at"] = timestamp
    overlay_digest = canonical_json_sha256(expected)
    return [
        {
            "ts": timestamp,
            "agent": str(os.environ.get("AI_NAME") or ""),
            "type": "completion_authority_install",
            "task_id": "LOOP-PROD-SIGNOFF-001",
            "completion_authority_sha256": completion_authority_sha256(catalog),
            "completion_overlay_sha256": overlay_digest,
            "message": "Installed exact catalog-bound completion authority overlay",
        }
    ], True


def validate_checkpoint_consumptions(
    state: dict[str, Any],
    catalog: dict[str, Any],
    catalog_digest: str,
) -> None:
    """Keep initial dispatch outside the protected completion-verifier authority."""

    state_key = str(catalog["completion_authority"]["checkpoint_consumption_state_key"])
    records = state.get(state_key)
    policy = catalog["completion_authority"].get("dispatcher_pre_completion_policy")
    if policy != "reject_preexisting_consumption_or_program_completed":
        raise DispatchError("dispatcher pre-completion policy is not exact")
    program_completed = state.get("program_completed")
    if records not in (None, {}) or (
        program_completed is not None and program_completed is not False
    ):
        raise DispatchError(
            "preexisting program completion or checkpoint consumption requires the "
            "protected LOOP-PROD-CLOSE-002 verifier; initial dispatcher refuses it"
        )


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
    additive_ids = {str(item) for item in catalog.get("additive_task_ids") or []}

    for task in tasks:
        task_id = task["id"]
        archive_state = archived_primary_status(task_id)
        if archive_state is not None:
            if task_id in additive_ids:
                archive_payload = read_json(ARCHIVE_ROOT / f"{task_id}.json")
                archived_task = archive_payload.get("task")
                if not isinstance(archived_task, dict):
                    raise DispatchError(
                        f"archive collision for additive task {task_id} has no task record"
                    )
                validate_additive_collision(
                    archived_task,
                    task,
                    catalog,
                    catalog_digest,
                    source="archive",
                )
            archived.append(f"{task_id}:{archive_state}")
            continue

        existing = active_by_id.get(task_id)
        if existing is not None:
            if task_id in additive_ids:
                validate_additive_collision(
                    existing,
                    task,
                    catalog,
                    catalog_digest,
                    source="active",
                )
            preserved.append(f"{task_id}:{existing.get('status', 'unknown')}")
            continue

        materialized = build_task(task, catalog, catalog_digest, timestamp)
        active_tasks.append(materialized)
        active_by_id[task_id] = materialized
        created.append(task_id)
        logs.append(
            {
                "ts": timestamp,
                "agent": str(os.environ.get("AI_NAME") or ""),
                "type": "assign",
                "task_id": task_id,
                "assigned_owner": str(materialized["owner"]),
                "assigned_reviewer": str(materialized["reviewer"]),
                "task_contract_sha256": task_contract_sha256(task),
                "source_ref_sha256": canonical_json_sha256(
                    materialized["source_ref"]
                ),
                "created_at": timestamp,
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
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate the catalog, task documents, repository routing, and DAG only.",
    )
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Acquire the shared read protocol, validate live dependencies, outbox, "
            "and proposed transaction, and print without writing."
        ),
    )
    mode.add_argument(
        "--apply",
        action="store_true",
        help="Apply the guarded transaction after bootstrap and authoritative dry-run.",
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
    catalog_digest = sha256_bytes(catalog_bytes)
    print(
        f"Catalog valid: program={catalog['program_id']} tasks={len(tasks)} "
        f"sha256={catalog_digest}"
    )
    if args.validate_only:
        return 0
    if not STATUS_PATH.is_file():
        raise DispatchError(f"status file not found: {STATUS_PATH}")
    actor = str(os.environ.get("AI_NAME") or "").strip()
    if actor not in set(catalog.get("allowed_owners") or []):
        raise DispatchError(
            "AI_NAME must name an allowed fleet actor for authoritative dry-run or apply"
        )
    protocol_module = load_runtime_lock_protocol(catalog)
    with shared_dispatch_locks(
        protocol_module,
        catalog,
        shared=bool(args.dry_run),
    ):
        original_signature = file_signature(STATUS_PATH)
        state = read_json(STATUS_PATH)
        pending = state.get("program_activity_outbox")
        if pending not in (None, {}, []):
            validated_pending = validate_activity_outbox(
                state,
                catalog,
                catalog_digest,
                require_current_catalog=False,
            )
            earliest = min(
                parse_activity_timestamp(event["ts"])
                for event in validated_pending["events"]
            )
            existing_events = activity_event_index(since=earliest)
            preflight_activity_events(validated_pending, existing_events)
            if args.apply and flush_activity_outbox(
                state,
                catalog,
                catalog_digest,
            ):
                print("Recovered pending activity audit outbox.")
                original_signature = file_signature(STATUS_PATH)

        validate_live_state(state, catalog, tasks)
        validate_checkpoint_consumptions(state, catalog, catalog_digest)
        validate_new_mutation_allowed(state)
        proposed = deepcopy(state)

        timestamp = iso_now()
        migrated, migration_logs, migration_changed = apply_catalog_migrations(
            proposed,
            catalog,
            catalog_digest,
            timestamp,
        )
        overlay_logs, overlay_changed = ensure_completion_overlay(
            proposed,
            catalog,
            catalog_digest,
            timestamp,
        )
        created, preserved, archived, logs, changed = materialize(
            proposed,
            tasks,
            catalog,
            catalog_digest,
            timestamp,
        )
        logs = [*migration_logs, *overlay_logs, *logs]
        changed = migration_changed or overlay_changed or changed
        report(created, preserved, archived, migrated, dry_run=args.dry_run)

        if not changed:
            print("No state changes required.")
            return 0
        enqueue_activity_outbox(
            proposed,
            logs,
            catalog=catalog,
            catalog_digest=catalog_digest,
        )
        transaction = validate_activity_outbox(
            proposed,
            catalog,
            catalog_digest,
            require_current_catalog=True,
        )
        existing_events = activity_event_index(
            since=min(
                parse_activity_timestamp(event["ts"])
                for event in transaction["events"]
            )
        )
        preflight_activity_events(transaction, existing_events)
        if file_signature(STATUS_PATH) != original_signature:
            raise DispatchError(
                "ai-status.json changed concurrently; no write performed, rerun dispatch"
            )
        if args.dry_run:
            print("Proposed outbox and audit sources validated; zero writes performed.")
            return 0

        atomic_write_json(STATUS_PATH, proposed)
        if os.environ.get("LOOP_PRODUCT_DISPATCH_FAIL_AFTER_STATUS_COMMIT") == "1":
            raise DispatchError(
                "injected failure after status commit; activity audit remains in outbox"
            )
        flush_activity_outbox(proposed, catalog, catalog_digest)
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
