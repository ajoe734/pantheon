#!/usr/bin/env python3
"""Validate and dispatch the 2026-07-13 loop product-level remediation DAG."""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import errno
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
from types import ModuleType
from typing import Any, Iterator, Mapping
from urllib import error as urllib_error
from urllib import request as urllib_request


REPO_ROOT = Path(__file__).resolve().parents[1]
ORCHESTRATOR_DIR = REPO_ROOT / ".orchestrator"
if str(ORCHESTRATOR_DIR) not in sys.path:
    sys.path.insert(0, str(ORCHESTRATOR_DIR))

from common import (
    activity_audit_source_paths_unlocked,
    append_activity_log_entries_unlocked,
    assert_activity_audit_stable_unlocked,
    prepare_activity_audit_unlocked,
    read_activity_audit_records,
)

DEFAULT_CATALOG_PATH = (
    REPO_ROOT
    / "docs"
    / "bff"
    / "execution-tasks"
    / "2026-07-13-loop-product-level-remediation"
    / "tasks.json"
)
DEFAULT_SEQUENCING_OVERLAY_PATH = (
    DEFAULT_CATALOG_PATH.parent / "sequencing-overlay-2026-07-16.json"
)
STATUS_ROOT = Path(
    os.path.expanduser(os.environ.get("PANTHEON_STATUS_ROOT", str(REPO_ROOT)))
).resolve()
STATUS_PATH = STATUS_ROOT / "ai-status.json"
LOG_PATH = STATUS_ROOT / "ai-activity-log.jsonl"
ARCHIVE_ROOT = STATUS_ROOT / "ai-task-archive" / "tasks"

SEQUENCING_ADDENDUM_PATH = (
    REPO_ROOT
    / "docs"
    / "04"
    / "pantheon_loop_product_level_remediation_2026-07-13"
    / "REMEDIATION_SEQUENCING_ADDENDUM_2026-07-16.md"
)
EXPECTED_SEQUENCING_SOURCE_HASHES = {
    "tasks_catalog_sha256": "44a893162da5779fc64292a70ba59fb7237eb4102ffb65f8e3ad3b64a8f31357",
    "sequencing_addendum_sha256": "9a3b735ac161b612e35a1d0e313cc7037da444f8b0311c623d27396a06d4b519",
    "merge_pr_3737_sha": "a4b5df9a51bc3da6df0d39d422d9db4edc553aba",
}
EXPECTED_SEQUENCING_OVERLAY_SHA256 = (
    "e506f62930bf0cb4f8cf6c3d1661b07ed638ad0903b8e640df3e178d7e9e7602"
)
EXPECTED_PRODUCT_EVIDENCE_SCHEMA_SHA256 = (
    "5340d8394a31fa9badf4519b2cdbac4f02317e9d930ddd250c8b7374015d3a73"
)
SEQUENCING_OVERLAY_KEYS = {
    "schema_version",
    "source_hashes",
    "acceptance_deferral",
    "release_gate",
    "g2_evidence_contract",
    "tasks",
}
SEQUENCING_TASK_ENTRY_KEYS = {
    "wave",
    "classification",
    "rationale",
    "original_depends_on",
    "amended_depends_on",
}
SEQUENCING_CLASSIFICATIONS = {
    "permitted before the paper-trade proof",
    "part of the G2 proof path",
    "deferred strict-auth/security/governance work",
    "final verification/closeout after the appropriate gate",
}
GATED_SEQUENCING_CLASSIFICATIONS = {
    "deferred strict-auth/security/governance work",
    "final verification/closeout after the appropriate gate",
}
PRE_G2_SEQUENCING_CLASSIFICATIONS = {
    "permitted before the paper-trade proof",
    "part of the G2 proof path",
}
RELEASE_GATE_KEYS = {
    "version",
    "gate_id",
    "gated_classifications",
    "gated_task_ids",
    "release_predicate",
    "pre_gate_action",
    "post_gate_action",
}
ACCEPTANCE_DEFERRAL_KEYS = {
    "version",
    "policy_id",
    "release_gate_id",
    "catalog_acceptance_immutable",
    "applies_to_classifications",
    "applies_to_task_ids",
    "deferred_dimensions",
    "retained_dimensions",
    "materialized_acceptance_action",
}
G2_EVIDENCE_CONTRACT_KEYS = {
    "version",
    "target_task",
    "target_task_original_contract_sha256",
    "target_task_amended_contract_sha256",
    "tasks_catalog_sha256",
    "sequencing_addendum_sha256",
    "merge_pr_3737_sha",
    "evidence_path",
    "closeout_manifest_path",
    "hosted_probe_path",
    "canonical_record_bundle_path",
    "canonical_source_resolution",
    "canonical_telemetry_dsn_env",
    "canonical_database_name",
    "canonical_database_role",
    "canonical_database_schema",
    "canonical_database_table",
    "canonical_projection_root_env",
    "canonical_projection_root",
    "artifact_commit_binding",
    "required_git_remote_url",
    "required_git_remote_ref",
    "required_github_api_base_url",
    "required_github_repository",
    "review_binding_schema",
    "bundle_digest_algorithm",
    "record_digest_algorithm",
    "record_bundle_schema",
    "hosted_probe_schema",
    "projection_manifest_schema",
    "journey_projection_schema",
    "loop_run_projection_schema",
    "required_target_environment",
    "required_record_environment",
    "required_execution_mode",
    "required_source_mode",
    "required_projection_stage_status",
    "required_projection_controller",
    "max_evidence_age_seconds",
    "max_chain_span_seconds",
    "max_future_skew_seconds",
    "stable_identity_fields",
    "event_order_contract",
    "record_event_types",
    "required_loop_run_status",
}
G2_STABLE_IDENTITY_FIELDS = (
    "tenant_id",
    "environment",
    "journey_id",
    "run_id",
    "loop_run_id",
    "signal_id",
    "strategy_id",
    "runtime_id",
    "binding_id",
    "capital_pool_id",
    "persona_id",
    "persona_capital_binding_id",
    "artifact_id",
    "artifact_version",
    "plan_id",
    "trace_id",
)
G2_EVIDENCE_KEYS = {
    "schema_version",
    "task_id",
    "program_id",
    "target_environment",
    "issued_at",
    "expires_at",
    "authority",
    "identity",
    "record_bundle",
    "hosted_probe",
    "records",
    "closeout_admission",
}
G2_AUTHORITY_KEYS = {
    "tasks_catalog_sha256",
    "sequencing_addendum_sha256",
    "merge_pr_3737_sha",
    "overlay_sha256",
    "target_task_original_contract_sha256",
    "target_task_amended_contract_sha256",
}
G2_RECORD_REFERENCE_KEYS = {"event_id", "event_type", "sha256"}
G2_PROJECTION_REFERENCE_KEYS = {
    "id",
    "sha256",
    "generation",
    "last_canonical_event_id",
}
G2_RECORDS_KEYS = {
    "signal",
    "order",
    "fill",
    "telemetry",
    "loop_run_projection",
}
G2_CLOSEOUT_ADMISSION_KEYS = {
    "review_file",
    "review_manifest_sha256",
    "review_manifest_sidecar_sha256",
    "task_snapshot_sha256",
    "reviewer",
    "review_verdict_sha256",
}
G2_RECORD_BUNDLE_KEYS = {
    "schema_version",
    "captured_at",
    "source",
    "rows",
    "projection",
}
G2_RECORD_BUNDLE_SOURCE_KEYS = {
    "store",
    "snapshot_isolation",
    "baseline_high_watermark",
    "source_high_watermark",
}
G2_RECORD_BUNDLE_PROJECTION_KEYS = {
    "manifest",
    "trade_journey_events",
    "loop_runs",
}
G2_SOURCE_ATTESTATION_KEYS = {
    "database",
    "role",
    "schema",
    "table",
    "projection_root",
    "live_source_high_watermark",
    "captured_generation_name",
    "current_generation_name",
    "current_projection_checkpoint",
    "rows_sha256",
    "projection_sha256",
}
G2_REVIEW_BINDING_KEYS = {
    "schema_version",
    "reviewer",
    "reviewed_at",
    "artifact_commit_sha",
    "artifact_sha256",
    "implementation_pr",
}
G2_REVIEW_ARTIFACT_DIGEST_FIELDS = {
    "g2_evidence_sha256",
    "canonical_record_bundle_sha256",
    "hosted_probe_sha256",
    "product_manifest_sha256",
    "product_manifest_sidecar_sha256",
}
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
RUNTIME_TASK_AUTHORITY_FIELDS = frozenset(
    {
        "task_class",
        "auto_created_by",
        "auto_generated",
        "delivery_layer",
        "mutates_canonical",
        "helper_kind",
        "completion_role",
        "execution_role",
        "review_role",
        "planner_controller_identity",
        "planner_may_edit_declared_product_artifacts",
        "formal_review_required",
    }
)
ACTIVITY_OUTBOX_SCHEMA_VERSION = 5
PROGRAM_GRAPH_BINDINGS_STATE_KEY = "program_catalog_graph_bindings"
PROGRAM_GRAPH_BINDING_SCHEMA_VERSION = 1
PROGRAM_GRAPH_RECOVERY_POLICY = "supervisor_signed_only"
PROGRAM_SEQUENCING_EPOCHS_STATE_KEY = "program_sequencing_epochs"
PROGRAM_SEQUENCING_RELEASES_STATE_KEY = "program_sequencing_releases"
SEQUENCING_EPOCH_SCHEMA_VERSION = 2
SEQUENCING_GATE_MARKER_SCHEMA_VERSION = 1
SEQUENCING_RELEASE_ADMISSION_FIELDS = frozenset(
    {
        "g2_evidence_sha256",
        "canonical_record_bundle_sha256",
        "canonical_source_snapshot_sha256",
        "canonical_source_attestation",
        "hosted_probe_sha256",
        "g2_artifact_commit_sha",
        "g2_artifact_merge_target_sha",
        "g2_authoritative_remote_head_sha",
        "g2_github_pr_snapshot_sha256",
        "product_manifest_sha256",
        "product_manifest_sidecar_sha256",
        "target_task_snapshot_sha256",
        "reviewer",
        "review_binding_sha256",
        "review_approval_event_sha256",
        "review_verdict_sha256",
        "g2_issued_at",
        "closeout_at",
    }
)
ACTIVITY_EVENT_TYPES = {
    "assign",
    "catalog_migration",
    "completion_authority_install",
    "sequencing_overlay_install",
    "sequencing_gate_release",
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
        "graph_binding_sha256",
        "graph_projection_sha256",
        "previous_graph_projection_sha256",
        "graph_binding_reason",
    },
    "sequencing_overlay_install": {
        "sequencing_epoch_sha256",
        "sequencing_overlay_sha256",
        "source_catalog_sha256",
        "effective_catalog_sha256",
        "task_transition_set_sha256",
    },
    "sequencing_gate_release": {
        "release_gate_id",
        "sequencing_overlay_sha256",
        "release_record_sha256",
        "released_task_transition_set_sha256",
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


def read_regular_bytes(path: Path, *, label: str) -> bytes:
    """Read a regular-file leaf without accepting a symlink or inode swap."""

    try:
        before = path.lstat()
    except FileNotFoundError as exc:
        raise DispatchError(f"{label} is missing: {path}") from exc
    except OSError as exc:
        raise DispatchError(f"{label} cannot be inspected: {path}") from exc
    if path.is_symlink() or not stat.S_ISREG(before.st_mode):
        raise DispatchError(f"{label} must be a regular file: {path}")
    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise DispatchError(f"{label} cannot be opened safely: {path}") from exc
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
            or (opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns)
            != (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
        ):
            raise DispatchError(f"{label} identity changed before read: {path}")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            raw = handle.read()
        opened_after = os.fstat(descriptor)
        after = path.lstat()
        if (
            path.is_symlink()
            or not stat.S_ISREG(after.st_mode)
            or (after.st_dev, after.st_ino) != (opened.st_dev, opened.st_ino)
            or (
                opened.st_size,
                opened.st_mtime_ns,
                opened.st_ctime_ns,
            )
            != (
                opened_after.st_size,
                opened_after.st_mtime_ns,
                opened_after.st_ctime_ns,
            )
            or (
                opened_after.st_size,
                opened_after.st_mtime_ns,
                opened_after.st_ctime_ns,
            )
            != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
        ):
            raise DispatchError(f"{label} identity changed during read: {path}")
        return raw
    except FileNotFoundError as exc:
        raise DispatchError(f"{label} disappeared during read: {path}") from exc
    finally:
        os.close(descriptor)


def read_regular_json(path: Path, *, label: str) -> tuple[dict[str, Any], bytes]:
    raw = read_regular_bytes(path, label=label)
    payload = strict_json_loads(raw, source=f"{label} {path}")
    if not isinstance(payload, dict):
        raise DispatchError(f"{label} must contain a JSON object: {path}")
    return payload, raw


def read_rooted_regular_bytes(
    root: Path,
    relative_value: Any,
    *,
    label: str,
    missing_ok: bool = False,
) -> bytes | None:
    """Read beneath one directory fd without following any path-component symlink."""

    relative = _safe_repo_relative_path(relative_value, label=f"{label} path")
    parts = Path(relative).parts
    if not parts:
        raise DispatchError(f"{label} path must name a file")
    directory_flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        directory_flags |= os.O_DIRECTORY
    if hasattr(os, "O_CLOEXEC"):
        directory_flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        directory_flags |= os.O_NOFOLLOW
    try:
        directory_fd = os.open(root.resolve(strict=True), directory_flags)
    except OSError as exc:
        raise DispatchError(f"{label} root cannot be opened safely") from exc
    descriptor: int | None = None
    try:
        for part in parts[:-1]:
            try:
                next_fd = os.open(
                    part,
                    directory_flags,
                    dir_fd=directory_fd,
                )
            except FileNotFoundError:
                if missing_ok:
                    return None
                raise DispatchError(
                    f"{label} parent cannot be opened safely"
                ) from None
            except OSError as exc:
                raise DispatchError(
                    f"{label} parent cannot be opened safely"
                ) from exc
            os.close(directory_fd)
            directory_fd = next_fd
        file_flags = os.O_RDONLY
        if hasattr(os, "O_CLOEXEC"):
            file_flags |= os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            file_flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(parts[-1], file_flags, dir_fd=directory_fd)
        except FileNotFoundError:
            if missing_ok:
                return None
            raise DispatchError(f"{label} cannot be opened safely") from None
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise DispatchError(f"{label} must be a regular file") from exc
            raise DispatchError(f"{label} cannot be opened safely") from exc
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise DispatchError(f"{label} must be a regular file")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            raw = handle.read()
        after = os.fstat(descriptor)
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise DispatchError(f"{label} changed during read")
        return raw
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(directory_fd)


def read_rooted_regular_json(
    root: Path,
    relative_value: Any,
    *,
    label: str,
) -> tuple[dict[str, Any], bytes]:
    raw = read_rooted_regular_bytes(root, relative_value, label=label)
    if raw is None:
        raise DispatchError(f"{label} cannot be opened safely")
    payload = strict_json_loads(raw, source=label)
    if not isinstance(payload, dict):
        raise DispatchError(f"{label} must contain a JSON object")
    return payload, raw


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
    # Route through the no-follow archive reader: `path.is_file()` /
    # `read_json()` both follow symlinks, which would let a symlinked archive
    # leaf report an arbitrary external status.
    payload = read_canonical_archive_payload(path)
    if payload is None:
        return ""
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
    if read_canonical_archive_payload(archived) is not None:
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


def _sanitized_git_environment() -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith("GIT_")
    }
    environment.update(
        {
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
        }
    )
    return environment


def _git_output(root: Path, *args: str) -> bytes:
    try:
        result = subprocess.run(
            [
                "git",
                "--no-replace-objects",
                "-c",
                "credential.helper=",
                "-C",
                str(root),
                *args,
            ],
            check=False,
            capture_output=True,
            env=_sanitized_git_environment(),
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DispatchError("repository git verification is unavailable") from exc
    if result.returncode != 0:
        raise DispatchError(
            "repository git verification failed: " + " ".join(args[:2])
        )
    return result.stdout


def _validate_git_repository_trust(root: Path) -> None:
    try:
        expected_root = root.resolve(strict=True)
    except OSError as exc:
        raise DispatchError("G2 Git repository root is unavailable") from exc
    top = Path(
        _git_output(root, "rev-parse", "--show-toplevel")
        .decode("utf-8")
        .strip()
    ).resolve(strict=True)
    git_dir = Path(
        _git_output(root, "rev-parse", "--absolute-git-dir")
        .decode("utf-8")
        .strip()
    ).resolve(strict=True)
    common_value = (
        _git_output(root, "rev-parse", "--git-common-dir")
        .decode("utf-8")
        .strip()
    )
    common_dir = Path(common_value)
    if not common_dir.is_absolute():
        common_dir = (root / common_dir).resolve(strict=True)
    else:
        common_dir = common_dir.resolve(strict=True)
    if (
        top != expected_root
        or not git_dir.is_dir()
        or not common_dir.is_dir()
        or _git_output(root, "rev-parse", "--is-shallow-repository").strip()
        != b"false"
        or _git_output(root, "replace", "-l").strip()
    ):
        raise DispatchError("G2 Git repository trust policy is not satisfied")
    for relative in ("info/grafts", "objects/info/alternates"):
        raw = (
            _git_output(root, "rev-parse", "--git-path", relative)
            .decode("utf-8")
            .strip()
        )
        path = Path(raw)
        if not path.is_absolute():
            path = root / path
        try:
            if path.exists() and path.read_bytes().strip():
                raise DispatchError(
                    "G2 Git repository cannot use grafts or object alternates"
                )
        except OSError as exc:
            raise DispatchError("G2 Git repository metadata is unreadable") from exc


def _resolve_authoritative_git_remote_ref(
    remote_url: str,
    remote_ref: str,
) -> str:
    try:
        result = subprocess.run(
            [
                "git",
                "--no-replace-objects",
                "-c",
                "credential.helper=",
                "ls-remote",
                "--refs",
                remote_url,
                remote_ref,
            ],
            check=False,
            capture_output=True,
            env=_sanitized_git_environment(),
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DispatchError("G2 authoritative Git remote is unavailable") from exc
    if result.returncode != 0:
        raise DispatchError("G2 authoritative Git remote query failed")
    rows = [line.split() for line in result.stdout.decode("utf-8").splitlines()]
    if (
        len(rows) != 1
        or len(rows[0]) != 2
        or rows[0][1] != remote_ref
        or not _is_lower_hex(rows[0][0], 40)
    ):
        raise DispatchError("G2 authoritative Git remote response is not exact")
    return rows[0][0]


def _github_api_json(url: str) -> dict[str, Any]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "pantheon-g2-evidence-validator",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = str(os.environ.get("GITHUB_TOKEN") or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        with urllib_request.urlopen(
            urllib_request.Request(url, headers=headers), timeout=30
        ) as response:
            raw = response.read()
    except (OSError, urllib_error.URLError, ValueError) as exc:
        raise DispatchError("G2 authoritative GitHub query failed") from exc
    payload = strict_json_loads(raw, source="G2 authoritative GitHub response")
    if not isinstance(payload, dict):
        raise DispatchError("G2 authoritative GitHub response is not an object")
    return payload


def _resolve_authoritative_github_pr(
    contract: Mapping[str, Any],
    pull_request_number: int,
) -> dict[str, Any]:
    api_base = str(contract.get("required_github_api_base_url") or "")
    repository = str(contract.get("required_github_repository") or "")
    pull = _github_api_json(
        f"{api_base}/repos/{repository}/pulls/{pull_request_number}"
    )
    checks_payload = _github_api_json(
        f"{api_base}/repos/{repository}/commits/"
        f"{str(((pull.get('head') or {}).get('sha')) or '')}/check-runs?per_page=100"
    )
    check_runs = checks_payload.get("check_runs")
    if not isinstance(check_runs, list):
        raise DispatchError("G2 authoritative GitHub checks are missing")
    checks = sorted(
        [
            {
                "name": str(row.get("name") or ""),
                "conclusion": str(row.get("conclusion") or ""),
            }
            for row in check_runs
            if isinstance(row, dict)
        ],
        key=lambda row: (row["name"], row["conclusion"]),
    )
    return {
        "repository": repository,
        "number": pull.get("number"),
        "url": pull.get("html_url"),
        "state": pull.get("state"),
        "merged": pull.get("merged"),
        "merged_at": pull.get("merged_at"),
        "base": (pull.get("base") or {}).get("ref"),
        "head_sha": (pull.get("head") or {}).get("sha"),
        "merge_sha": pull.get("merge_commit_sha"),
        "checks": checks,
    }


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
    """Return disjoint rotated history followed by the active audit log."""

    # Correctness cannot depend on archive mtimes: rotation, restore, or an
    # operator copy can preserve an old mtime while containing a pending event.
    # Scan every source while the audit sidecar is held so global event-ID
    # uniqueness remains exact across active and rotated history.
    _ = since
    return activity_audit_source_paths_unlocked(LOG_PATH)


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
            if previous_digest is not None:
                detail = (
                    "conflicting"
                    if previous_digest != payload_digest
                    else "duplicate"
                )
                raise DispatchError(
                    f"{detail} activity audit event_id {event_id} across rotated logs"
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
    append_activity_log_entries_unlocked(LOG_PATH, entries)


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
    graph_bindings = state.get(PROGRAM_GRAPH_BINDINGS_STATE_KEY) or {}
    if not isinstance(graph_bindings, dict):
        raise DispatchError("affected-state projection graph bindings are invalid")
    sequencing_epochs = state.get(PROGRAM_SEQUENCING_EPOCHS_STATE_KEY) or {}
    sequencing_releases = state.get(PROGRAM_SEQUENCING_RELEASES_STATE_KEY) or {}
    if not isinstance(sequencing_epochs, dict) or not isinstance(
        sequencing_releases, dict
    ):
        raise DispatchError("affected-state sequencing records are invalid")
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
            graph_binding = graph_bindings.get(program_id)
            if not isinstance(overlay, dict) or not isinstance(graph_binding, dict):
                raise DispatchError("affected completion overlay or graph binding is missing")
            items.append(
                {
                    "event_type": event_type,
                    "task_id": task_id,
                    "completion_overlay": deepcopy(overlay),
                    "graph_binding": deepcopy(graph_binding),
                }
            )
        elif event_type in {
            "sequencing_overlay_install",
            "sequencing_gate_release",
        }:
            record = (
                sequencing_epochs.get(program_id)
                if event_type == "sequencing_overlay_install"
                else sequencing_releases.get(program_id)
            )
            transition_field = (
                "task_transitions"
                if event_type == "sequencing_overlay_install"
                else "released_task_transitions"
            )
            transitions = record.get(transition_field) if isinstance(record, dict) else None
            if not isinstance(record, dict) or not isinstance(transitions, list):
                raise DispatchError("affected sequencing record is missing")
            snapshots: list[dict[str, Any]] = []
            for transition in transitions:
                task = (
                    active_by_id.get(str(transition.get("task_id") or ""))
                    if isinstance(transition, dict)
                    else None
                )
                if not isinstance(task, dict):
                    raise DispatchError("affected sequencing task is missing")
                snapshots.append(deepcopy(task))
            items.append(
                {
                    "event_type": event_type,
                    "task_id": task_id,
                    "record": deepcopy(record),
                    "tasks": snapshots,
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
    _, archived_by_id = _program_graph_sources(state, catalog)
    records = state.get("program_catalog_migrations") or []
    if not isinstance(records, list) or any(not isinstance(record, dict) for record in records):
        raise DispatchError("program_activity_outbox current migration state is invalid")
    record_by_id = {str(record.get("id") or ""): record for record in records}
    overlay_key = str(catalog["completion_authority"]["live_overlay_state_key"])
    overlays = state.get(overlay_key) or {}
    if not isinstance(overlays, dict):
        raise DispatchError("program_activity_outbox current overlay state is invalid")
    graph_bindings = state.get(PROGRAM_GRAPH_BINDINGS_STATE_KEY) or {}
    if not isinstance(graph_bindings, dict):
        raise DispatchError("program_activity_outbox current graph binding state is invalid")
    sequencing_epochs = state.get(PROGRAM_SEQUENCING_EPOCHS_STATE_KEY) or {}
    sequencing_releases = state.get(PROGRAM_SEQUENCING_RELEASES_STATE_KEY) or {}
    if not isinstance(sequencing_epochs, dict) or not isinstance(
        sequencing_releases, dict
    ):
        raise DispatchError("program_activity_outbox sequencing state is invalid")
    for event, item in zip(raw_events, projection["items"], strict=True):
        event_type = str(event["type"])
        task_id = str(event["task_id"])
        common = {"event_type", "task_id"}
        expected_fields = (
            common | {"task"}
            if event_type == "assign"
            else common | {"task", "migration_record"}
            if event_type == "catalog_migration"
            else common | {"record", "tasks"}
            if event_type
            in {"sequencing_overlay_install", "sequencing_gate_release"}
            else common | {"completion_overlay", "graph_binding"}
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
            current_task = active_by_id.get(task_id) or archived_by_id.get(task_id)
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
        elif event_type == "completion_authority_install":
            overlay = item.get("completion_overlay")
            graph_binding = item.get("graph_binding")
            current_overlay = overlays.get(program_id)
            current_graph_binding = graph_bindings.get(program_id)
            if (
                not isinstance(overlay, dict)
                or not isinstance(graph_binding, dict)
                or canonical_json_sha256(overlay)
                != event.get("completion_overlay_sha256")
                or canonical_json_sha256(graph_binding)
                != event.get("graph_binding_sha256")
                or graph_binding.get("graph_projection_sha256")
                != event.get("graph_projection_sha256")
                or graph_binding.get("previous_graph_projection_sha256")
                != event.get("previous_graph_projection_sha256")
                or graph_binding.get("binding_reason")
                != event.get("graph_binding_reason")
                or overlay.get("completion_authority_sha256")
                != event.get("completion_authority_sha256")
                or current_overlay != overlay
                or current_graph_binding != graph_binding
            ):
                raise DispatchError(
                    "program_activity_outbox completion overlay or graph binding drift"
                )
        else:
            record = item.get("record")
            snapshots = item.get("tasks")
            current_record = (
                sequencing_epochs.get(program_id)
                if event_type == "sequencing_overlay_install"
                else sequencing_releases.get(program_id)
            )
            digest_field = (
                "sequencing_epoch_sha256"
                if event_type == "sequencing_overlay_install"
                else "release_record_sha256"
            )
            transition_field = (
                "task_transitions"
                if event_type == "sequencing_overlay_install"
                else "released_task_transitions"
            )
            transitions = record.get(transition_field) if isinstance(record, dict) else None
            if (
                not isinstance(record, dict)
                or record != current_record
                or canonical_json_sha256(record) != event.get(digest_field)
                or not isinstance(snapshots, list)
                or not isinstance(transitions, list)
                or len(snapshots) != len(transitions)
            ):
                raise DispatchError("program_activity_outbox sequencing record drift")
            for transition, snapshot in zip(transitions, snapshots, strict=True):
                transition_task_id = (
                    str(transition.get("task_id") or "")
                    if isinstance(transition, dict)
                    else ""
                )
                current_task = active_by_id.get(transition_task_id)
                if (
                    not isinstance(snapshot, dict)
                    or snapshot.get("id") != transition_task_id
                    or canonical_json_sha256(snapshot)
                    != transition.get("after_task_snapshot_sha256")
                    or not isinstance(current_task, dict)
                    or (
                        require_exact_current
                        and current_task != snapshot
                    )
                ):
                    raise DispatchError(
                        "program_activity_outbox sequencing task drift"
                    )


def enqueue_activity_outbox(
    state: dict[str, Any],
    entries: list[dict[str, Any]],
    *,
    catalog: dict[str, Any],
    catalog_digest: str,
) -> None:
    pending = state.get("program_activity_outbox")
    if pending is not None:
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
    if pending.get("schema_version") == 4:
        raise DispatchError(
            "legacy program_activity_outbox schema 4 requires supervisor-signed "
            "recovery before graph-bound dispatch"
        )
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
        elif event_type == "completion_authority_install":
            if entry.get("message") != (
                "Installed exact catalog-bound completion authority and task graph"
            ) or any(
                len(str(entry.get(field) or "")) != 64
                for field in (
                    "completion_authority_sha256",
                    "completion_overlay_sha256",
                    "graph_binding_sha256",
                    "graph_projection_sha256",
                    "previous_graph_projection_sha256",
                )
            ) or not str(entry.get("graph_binding_reason") or "").strip():
                raise DispatchError("completion overlay event binding is invalid")
        elif event_type == "sequencing_overlay_install":
            if (
                entry.get("message")
                != "Installed exact sequencing overlay epoch"
                or any(
                    not _is_lower_hex(entry.get(field), 64)
                    for field in (
                        "sequencing_epoch_sha256",
                        "sequencing_overlay_sha256",
                        "source_catalog_sha256",
                        "effective_catalog_sha256",
                        "task_transition_set_sha256",
                    )
                )
            ):
                raise DispatchError("sequencing overlay event binding is invalid")
        elif event_type == "sequencing_gate_release":
            if (
                entry.get("message")
                != "Released exact sequencing gate after G2 admission"
                or not isinstance(entry.get("release_gate_id"), str)
                or not str(entry["release_gate_id"]).strip()
                or any(
                    not _is_lower_hex(entry.get(field), 64)
                    for field in (
                        "sequencing_overlay_sha256",
                        "release_record_sha256",
                        "released_task_transition_set_sha256",
                    )
                )
            ):
                raise DispatchError("sequencing release event binding is invalid")
        else:
            raise DispatchError("program_activity_outbox event type is unsupported")

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
    if pending is None:
        return False
    # Keep the semantic recovery gate at the side-effect boundary.  Callers may
    # preflight a transaction earlier, but no sequencing audit may be appended
    # or cleared without revalidating the committed epoch/release snapshot.
    pending = validate_pending_sequencing_recovery(
        state,
        catalog,
        catalog_digest,
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


def validate_pending_sequencing_recovery(
    state: dict[str, Any],
    catalog: dict[str, Any],
    catalog_digest: str,
) -> dict[str, Any]:
    """Validate committed sequencing semantics before clearing its audit outbox."""

    pending = validate_activity_outbox(
        state,
        catalog,
        catalog_digest,
        require_current_catalog=False,
    )
    if (
        catalog.get("overlay_applied") is not True
        or pending.get("catalog_sha256") != catalog_digest
    ):
        return pending
    pending = validate_activity_outbox(
        state,
        catalog,
        catalog_digest,
        require_current_catalog=True,
    )
    if validate_sequencing_epoch_record(state, catalog, catalog_digest) is None:
        raise DispatchError(
            "pending sequencing recovery is missing its immutable epoch"
        )
    releases = state.get(PROGRAM_SEQUENCING_RELEASES_STATE_KEY) or {}
    if not isinstance(releases, dict):
        raise DispatchError("program sequencing releases must be an object")
    if str(catalog["program_id"]) in releases and (
        validate_sequencing_release_record(state, catalog, catalog_digest) is None
    ):
        raise DispatchError(
            "pending sequencing recovery has no immutable release"
        )
    return pending


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


def expected_runtime_task_authority(
    task_id: str,
    catalog: dict[str, Any],
) -> dict[str, Any]:
    return {
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


def has_exact_runtime_task_authority(
    task: Mapping[str, Any],
    task_id: str,
    catalog: dict[str, Any],
) -> bool:
    expected = expected_runtime_task_authority(task_id, catalog)
    return all(task.get(field) == expected[field] for field in expected)


def build_task(
    task: dict[str, Any],
    catalog: dict[str, Any],
    catalog_digest: str,
    timestamp: str,
) -> dict[str, Any]:
    result = deepcopy(task)
    task_id = str(task["id"])
    selected_catalog_path = catalog_path()
    try:
        catalog_ref = str(selected_catalog_path.relative_to(REPO_ROOT))
    except ValueError:
        catalog_ref = str(selected_catalog_path)
    result.update(
        {
            "created_at": timestamp,
            "last_update": timestamp,
            **expected_runtime_task_authority(task_id, catalog),
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
    if catalog.get("overlay_applied") is True:
        source_hashes = catalog.get("source_hashes") or {}
        release_gate = catalog.get("release_gate") or {}
        g2_contract = catalog.get("g2_evidence_contract") or {}
        sequencing_entry = (catalog.get("sequencing_entries") or {}).get(
            task_id, {}
        )
        deferral = catalog.get("acceptance_deferral") or {}
        deferral_projection: dict[str, Any] | None = None
        if str(task["id"]) in set(deferral.get("applies_to_task_ids") or []):
            deferral_projection = {
                "policy_id": deferral.get("policy_id"),
                "release_gate_id": deferral.get("release_gate_id"),
                "catalog_acceptance_immutable": deferral.get(
                    "catalog_acceptance_immutable"
                ),
                "deferred_dimensions": deepcopy(
                    deferral.get("deferred_dimensions")
                ),
                "retained_dimensions": deepcopy(
                    deferral.get("retained_dimensions")
                ),
                "materialized_acceptance_action": deferral.get(
                    "materialized_acceptance_action"
                ),
            }
            result["acceptance_deferral"] = deepcopy(deferral_projection)
        result["source_ref"].update(
            {
                "source_catalog_sha256": source_hashes.get(
                    "tasks_catalog_sha256"
                ),
                "sequencing_addendum_sha256": source_hashes.get(
                    "sequencing_addendum_sha256"
                ),
                "merge_pr_3737_sha": source_hashes.get("merge_pr_3737_sha"),
                "sequencing_overlay_sha256": catalog.get(
                    "sequencing_overlay_sha256"
                ),
                "original_task_contract_sha256": (
                    catalog.get("original_task_contract_sha256s") or {}
                ).get(str(task["id"])),
                "amended_task_contract_sha256": task_contract_sha256(task),
                "sequencing_classification": sequencing_entry.get(
                    "classification"
                ),
                "release_gate_id": release_gate.get("gate_id"),
                "acceptance_sha256": canonical_json_sha256(task["acceptance"]),
                "acceptance_deferral_sha256": (
                    canonical_json_sha256(deferral_projection)
                    if deferral_projection is not None
                    else None
                ),
                "g2_release_checkpoint": (
                    str(task["id"]) == str(g2_contract.get("target_task") or "")
                ),
            }
        )
    return result


def validate_existing_task_provenance(
    existing: dict[str, Any],
    task: dict[str, Any],
    catalog: dict[str, Any],
    catalog_digest: str,
    *,
    source: str,
) -> None:
    """Reject an existing catalog task row with missing or foreign source_ref.

    Base-catalog collisions retain historical compatibility. Once the
    sequencing overlay is active, every preserved row must match the amended
    task contract and the exact overlay epoch; an older row requires a
    separately governed migration and cannot silently bypass this gate.
    """

    task_id = str(task["id"])
    source_ref = existing.get("source_ref")
    if (
        not isinstance(source_ref, dict)
        or not source_ref
        or source_ref.get("program_id") != catalog.get("program_id")
        or not source_ref.get("catalog_sha256")
    ):
        raise DispatchError(
            f"missing or mismatched source_ref provenance for {source} catalog "
            f"task {task_id}; refusing to preserve foreign or tampered "
            "task/runtime state"
    )
    if catalog.get("overlay_applied") is True:
        expected_contract = task_contract_sha256(task)
        expected_runtime = build_task(
            task,
            catalog,
            catalog_digest,
            str(existing.get("created_at") or existing.get("last_update") or iso_now()),
        )
        expected_source_ref = expected_runtime["source_ref"]
        if (
            task_contract_sha256(existing) != expected_contract
            or source_ref != expected_source_ref
            or existing.get("acceptance_deferral")
            != expected_runtime.get("acceptance_deferral")
        ):
            raise DispatchError(
                f"{source} catalog task {task_id} is not bound to the exact "
                "sequencing overlay epoch"
            )


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
    metadata_matches = has_exact_runtime_task_authority(
        existing,
        task_id,
        catalog,
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


def catalog_graph_projection(
    catalog: dict[str, Any],
    catalog_digest: str,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "program_id": str(catalog["program_id"]),
        "catalog_sha256": catalog_digest,
        "task_count": len(catalog["tasks"]),
        "external_dependencies": [
            str(item) for item in catalog["external_dependencies"]
        ],
        "additive_task_ids": [
            str(item) for item in catalog.get("additive_task_ids") or []
        ],
        "tasks": [
            {
                "task_id": str(task["id"]),
                "task_contract_sha256": task_contract_sha256(task),
                "depends_on": [str(item) for item in task["depends_on"]],
            }
            for task in catalog["tasks"]
        ],
    }


def historical_catalog_graph_projection(catalog: dict[str, Any]) -> dict[str, Any]:
    """Return the one exact pre-addendum graph accepted for catalog migration."""

    migrations = catalog.get("catalog_migrations") or []
    if len(migrations) != 1:
        raise DispatchError("historical graph requires exactly one catalog migration")
    migration = migrations[0]
    source_bytes = _git_output(
        REPO_ROOT,
        "show",
        f"{migration['from_catalog_git_commit']}:{migration['from_catalog_path']}",
    )
    if sha256_bytes(source_bytes) != migration["from_catalog_sha256"]:
        raise DispatchError("historical catalog graph Git object digest mismatch")
    source = strict_json_loads(source_bytes, source="historical catalog graph")
    if (
        not isinstance(source, dict)
        or source.get("schema_version") != 1
        or source.get("program_id") != catalog.get("program_id")
        or not isinstance(source.get("tasks"), list)
        or not isinstance(source.get("external_dependencies"), list)
    ):
        raise DispatchError("historical catalog graph source is not exact")
    rows = [
        {
            "task_id": str(task["id"]),
            "task_contract_sha256": task_contract_sha256(task),
            "depends_on": [str(item) for item in task["depends_on"]],
        }
        for task in source["tasks"]
    ]
    return {
        "schema_version": 1,
        "program_id": str(catalog["program_id"]),
        "catalog_sha256": str(migration["from_catalog_sha256"]),
        "task_count": len(rows),
        "external_dependencies": [
            str(item) for item in source["external_dependencies"]
        ],
        "additive_task_ids": [
            str(item) for item in source.get("additive_task_ids") or []
        ],
        "tasks": rows,
    }


def build_program_graph_binding(
    projection: dict[str, Any],
    *,
    bound_at: str,
    binding_reason: str,
    previous_graph_projection_sha256: str,
) -> dict[str, Any]:
    parse_activity_timestamp(bound_at)
    if not binding_reason.strip():
        raise DispatchError("program graph binding reason must be non-empty")
    if not _is_lower_hex(previous_graph_projection_sha256, 64):
        raise DispatchError("program graph previous projection digest is invalid")
    graph_projection_sha256 = canonical_json_sha256(projection)
    payload = {
        "schema_version": PROGRAM_GRAPH_BINDING_SCHEMA_VERSION,
        "program_id": str(projection["program_id"]),
        "catalog_sha256": str(projection["catalog_sha256"]),
        "graph_projection": deepcopy(projection),
        "graph_projection_sha256": graph_projection_sha256,
        "previous_graph_projection_sha256": previous_graph_projection_sha256,
        "bound_at": bound_at,
        "binding_reason": binding_reason,
        "missing_binding_recovery_policy": PROGRAM_GRAPH_RECOVERY_POLICY,
    }
    payload["binding_id"] = "loop-product-graph-binding-" + canonical_json_sha256(
        payload
    )
    return payload


def validate_program_graph_binding(binding: Any) -> dict[str, Any]:
    required_fields = {
        "schema_version",
        "program_id",
        "catalog_sha256",
        "graph_projection",
        "graph_projection_sha256",
        "previous_graph_projection_sha256",
        "bound_at",
        "binding_reason",
        "missing_binding_recovery_policy",
        "binding_id",
    }
    if not isinstance(binding, dict) or set(binding) != required_fields:
        raise DispatchError("program catalog graph binding schema is not exact")
    projection = binding.get("graph_projection")
    if (
        binding.get("schema_version") != PROGRAM_GRAPH_BINDING_SCHEMA_VERSION
        or not isinstance(projection, dict)
        or set(projection)
        != {
            "schema_version",
            "program_id",
            "catalog_sha256",
            "task_count",
            "external_dependencies",
            "additive_task_ids",
            "tasks",
        }
        or projection.get("schema_version") != 1
        or binding.get("program_id") != projection.get("program_id")
        or binding.get("catalog_sha256") != projection.get("catalog_sha256")
        or binding.get("graph_projection_sha256")
        != canonical_json_sha256(projection)
        or binding.get("missing_binding_recovery_policy")
        != PROGRAM_GRAPH_RECOVERY_POLICY
        or not isinstance(projection.get("external_dependencies"), list)
        or not isinstance(projection.get("additive_task_ids"), list)
        or any(
            not isinstance(item, str) or not item.strip()
            for field in ("external_dependencies", "additive_task_ids")
            for item in projection[field]
        )
        or any(
            len(projection[field]) != len(set(projection[field]))
            for field in ("external_dependencies", "additive_task_ids")
        )
        or not _is_lower_hex(binding.get("previous_graph_projection_sha256"), 64)
        or not isinstance(binding.get("binding_reason"), str)
        or not str(binding["binding_reason"]).strip()
    ):
        raise DispatchError("program catalog graph binding contract mismatch")
    parse_activity_timestamp(binding.get("bound_at"))
    rows = projection.get("tasks")
    if (
        not isinstance(rows, list)
        or projection.get("task_count") != len(rows)
        or not rows
    ):
        raise DispatchError("program catalog graph projection task count is invalid")
    task_ids: list[str] = []
    for row in rows:
        if (
            not isinstance(row, dict)
            or set(row) != {"task_id", "task_contract_sha256", "depends_on"}
            or not isinstance(row.get("task_id"), str)
            or not str(row["task_id"]).strip()
            or not _is_lower_hex(row.get("task_contract_sha256"), 64)
            or not isinstance(row.get("depends_on"), list)
            or any(
                not isinstance(item, str) or not item.strip()
                for item in row["depends_on"]
            )
            or len(row["depends_on"]) != len(set(row["depends_on"]))
        ):
            raise DispatchError("program catalog graph projection row is invalid")
        task_ids.append(str(row["task_id"]))
    if len(task_ids) != len(set(task_ids)):
        raise DispatchError("program catalog graph projection task IDs are duplicated")
    unsigned = {key: deepcopy(value) for key, value in binding.items() if key != "binding_id"}
    expected_binding_id = "loop-product-graph-binding-" + canonical_json_sha256(
        unsigned
    )
    if binding.get("binding_id") != expected_binding_id:
        raise DispatchError("program catalog graph binding ID mismatch")
    return binding


def read_canonical_archive_payload(path: Path) -> dict[str, Any] | None:
    """Read an archive beneath its status root without following any symlink."""

    try:
        relative = path.relative_to(ARCHIVE_ROOT.parent.parent)
    except ValueError as exc:
        raise DispatchError("canonical task archive path escapes its status root") from exc
    raw = read_rooted_regular_bytes(
        ARCHIVE_ROOT.parent.parent,
        str(relative),
        label="canonical task archive",
        missing_ok=True,
    )
    if raw is None:
        return None
    payload = strict_json_loads(raw, source="canonical task archive")
    if not isinstance(payload, dict):
        raise DispatchError("canonical task archive must contain a JSON object")
    return payload


def _program_graph_sources(
    state: dict[str, Any],
    catalog: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    active = state.get("tasks")
    if not isinstance(active, list) or any(not isinstance(task, dict) for task in active):
        raise DispatchError("program graph validation requires exact active task objects")
    current_ids = {str(task["id"]) for task in catalog["tasks"]}
    active_by_id = {
        str(task["id"]): task
        for task in active
        if str(task.get("id") or "") in current_ids
    }
    archived_by_id: dict[str, dict[str, Any]] = {}
    for task_id in sorted(current_ids):
        path = ARCHIVE_ROOT / f"{task_id}.json"
        payload = read_canonical_archive_payload(path)
        if payload is None:
            continue
        archived_task = payload.get("task")
        terminal_status = archive_status(path)
        if (
            set(payload)
            != {
                "version",
                "task_id",
                "archived_at",
                "terminal_status",
                "terminal_outcome",
                "task",
                "handoffs",
                "blockers",
            }
            or payload.get("version") != 1
            or payload.get("task_id") != task_id
            or terminal_status != "done"
            or payload.get("terminal_outcome") not in {"completed", "superseded"}
            or not isinstance(payload.get("handoffs"), list)
            or not isinstance(payload.get("blockers"), list)
            or not isinstance(archived_task, dict)
            or archived_task.get("id") != task_id
            or archived_task.get("status") != "done"
            or str(archived_task.get("terminal_outcome") or "completed")
            != payload.get("terminal_outcome")
        ):
            raise DispatchError(
                f"terminal archive for {task_id} must retain an exact full task contract"
            )
        parse_activity_timestamp(payload.get("archived_at"))
        archived_by_id[task_id] = archived_task
    duplicate_sources = sorted(set(active_by_id) & set(archived_by_id))
    if duplicate_sources:
        raise DispatchError(
            "program tasks exist in both active and archive state: "
            + ", ".join(duplicate_sources)
        )
    return active_by_id, archived_by_id


def validate_program_graph_sources(
    state: dict[str, Any],
    catalog: dict[str, Any],
    projection: dict[str, Any],
) -> None:
    active_by_id, archived_by_id = _program_graph_sources(state, catalog)
    observed_ids = set(active_by_id) | set(archived_by_id)
    rows = {str(row["task_id"]): row for row in projection["tasks"]}
    if observed_ids != set(rows):
        missing = sorted(set(rows) - observed_ids)
        foreign = sorted(observed_ids - set(rows))
        raise DispatchError(
            "program catalog graph source set mismatch: "
            f"missing={missing} foreign={foreign}"
        )
    for task_id, row in rows.items():
        source = active_by_id.get(task_id) or archived_by_id.get(task_id)
        if not isinstance(source, dict):
            raise DispatchError(f"program catalog graph task is missing: {task_id}")
        if (
            task_contract_sha256(source) != row["task_contract_sha256"]
            or source.get("depends_on") != row["depends_on"]
        ):
            raise DispatchError(
                f"program catalog graph contract or dependency mismatch: {task_id}"
            )


def expected_completion_overlay(
    catalog: dict[str, Any],
    catalog_digest: str,
    graph_binding: dict[str, Any],
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
        "catalog_graph_binding_state_key": PROGRAM_GRAPH_BINDINGS_STATE_KEY,
        "catalog_graph_binding_sha256": canonical_json_sha256(graph_binding),
        "catalog_graph_projection_sha256": graph_binding[
            "graph_projection_sha256"
        ],
        "missing_catalog_graph_binding_recovery_policy": (
            PROGRAM_GRAPH_RECOVERY_POLICY
        ),
        "checkpoint_consumption_required": True,
        "checkpoint_consumption_record_contract_sha256": canonical_json_sha256(
            catalog["completion_authority"]["checkpoint_consumption_record_contract"]
        ),
    }


def validate_historical_program_provenance(
    state: dict[str, Any],
    catalog: dict[str, Any],
    projection: dict[str, Any],
) -> None:
    migration = (catalog.get("catalog_migrations") or [None])[0]
    if not isinstance(migration, dict):
        raise DispatchError("historical catalog migration is missing")
    fixture = validate_preimage_fixture(migration)
    expected_source_ref = fixture["historical_source_ref"]["value"]
    expected_metadata = fixture["historical_dispatcher_metadata"]["value"]
    active_by_id, archived_by_id = _program_graph_sources(state, catalog)
    for row in projection["tasks"]:
        task_id = str(row["task_id"])
        task = active_by_id.get(task_id) or archived_by_id.get(task_id)
        if not isinstance(task, dict):
            raise DispatchError(f"historical program task is missing: {task_id}")
        metadata = {field: task.get(field) for field in expected_metadata}
        if task.get("source_ref") != expected_source_ref or metadata != expected_metadata:
            raise DispatchError(
                f"historical program provenance or dispatcher metadata changed: {task_id}"
            )


def program_activity_records(
    state: dict[str, Any],
    program_id: str,
) -> list[dict[str, Any]]:
    """Return unique persisted-or-pending program evidence under the audit lock."""

    # No-op reruns must still reject duplicate or payload-divergent event IDs.
    activity_event_index()
    records: list[dict[str, Any]] = []
    by_event_id: dict[str, dict[str, Any]] = {}

    def add_record(entry: dict[str, Any], *, source: str) -> None:
        catalog_digest = entry.get("catalog_sha256")
        event_type = entry.get("type")
        if not _is_lower_hex(catalog_digest, 64) or not isinstance(
            event_type,
            str,
        ) or not event_type.strip():
            raise DispatchError(f"program activity audit binding is incomplete: {source}")
        event_id = entry.get("event_id")
        if isinstance(event_id, str) and event_id.strip():
            previous = by_event_id.get(event_id)
            if previous is not None:
                if previous != entry:
                    raise DispatchError(
                        f"program activity event payload conflicts with pending proof: {event_id}"
                    )
                return
            by_event_id[event_id] = entry
        records.append(entry)

    for path in activity_log_sources():
        for line_number, line in enumerate(
            _read_activity_source(path).splitlines(),
            start=1,
        ):
            if not line.strip():
                continue
            entry = strict_json_loads(
                line,
                source=f"{path}:{line_number}",
            )
            if not isinstance(entry, dict):
                raise DispatchError("activity audit row must be an object")
            if entry.get("program_id") == program_id:
                add_record(entry, source=f"{path}:{line_number}")

    pending = state.get("program_activity_outbox")
    if isinstance(pending, dict) and pending.get("program_id") == program_id:
        events = pending.get("events")
        if not isinstance(events, list):
            raise DispatchError("program activity pending evidence is not a list")
        for ordinal, entry in enumerate(events):
            if not isinstance(entry, dict):
                raise DispatchError("program activity pending evidence is not an object")
            add_record(entry, source=f"program_activity_outbox.events[{ordinal}]")
    return records


def validate_program_graph_install_audit(
    records: list[dict[str, Any]],
    catalog: dict[str, Any],
    catalog_digest: str,
    binding: dict[str, Any],
    overlay: dict[str, Any],
) -> None:
    installs = [
        entry
        for entry in records
        if entry.get("type") == "completion_authority_install"
        and entry.get("catalog_sha256") == catalog_digest
    ]
    if len(installs) != 1:
        raise DispatchError(
            "program catalog graph epoch requires exactly one durable install audit event"
        )
    entry = installs[0]
    if set(entry) != (
        ACTIVITY_COMMON_EVENT_FIELDS
        | ACTIVITY_EVENT_EXTRA_FIELDS["completion_authority_install"]
    ):
        raise DispatchError("program catalog graph install audit schema is not exact")
    unsigned = {key: deepcopy(value) for key, value in entry.items() if key != "event_id"}
    expected_event_id = "loop-product-event-" + canonical_json_sha256(unsigned)
    if (
        entry.get("event_id") != expected_event_id
        or entry.get("program_id") != catalog.get("program_id")
        or entry.get("catalog_sha256") != catalog_digest
        or entry.get("task_id") != "LOOP-PROD-SIGNOFF-001"
        or entry.get("message")
        != "Installed exact catalog-bound completion authority and task graph"
        or entry.get("completion_authority_sha256")
        != completion_authority_sha256(catalog)
        or entry.get("completion_overlay_sha256")
        != canonical_json_sha256(overlay)
        or entry.get("graph_binding_sha256")
        != canonical_json_sha256(binding)
        or entry.get("graph_projection_sha256")
        != binding["graph_projection_sha256"]
        or entry.get("previous_graph_projection_sha256")
        != binding["previous_graph_projection_sha256"]
        or entry.get("graph_binding_reason") != binding["binding_reason"]
    ):
        raise DispatchError(
            "program catalog graph binding does not match its durable install audit event"
        )


def validate_program_graph_prestate(
    state: dict[str, Any],
    catalog: dict[str, Any],
    catalog_digest: str,
    *,
    source_catalog: dict[str, Any] | None = None,
    source_catalog_digest: str | None = None,
) -> str:
    program_id = str(catalog["program_id"])
    active_by_id, archived_by_id = _program_graph_sources(state, catalog)
    observed_ids = set(active_by_id) | set(archived_by_id)
    bindings = state.get(PROGRAM_GRAPH_BINDINGS_STATE_KEY)
    if bindings is None:
        bindings = {}
    if not isinstance(bindings, dict):
        raise DispatchError("program catalog graph bindings must be an object")
    binding = bindings.get(program_id)
    overlay_key = str(catalog["completion_authority"]["live_overlay_state_key"])
    overlays = state.get(overlay_key)
    if overlays is None:
        overlays = {}
    if not isinstance(overlays, dict):
        raise DispatchError("program completion authority overlays must be an object")
    overlay = overlays.get(program_id)
    migration_records = state.get("program_catalog_migrations")
    activity_records = program_activity_records(state, program_id)
    audit_catalog_digests = {
        str(entry["catalog_sha256"]) for entry in activity_records
    }
    audit_event_types = {str(entry["type"]) for entry in activity_records}

    if binding is None:
        if (
            not observed_ids
            and overlay is None
            and migration_records in (None, [])
            and not audit_catalog_digests
        ):
            return "fresh"
        graph_catalog = source_catalog or catalog
        historical_projection = historical_catalog_graph_projection(graph_catalog)
        historical_ids = {str(row["task_id"]) for row in historical_projection["tasks"]}
        if (
            observed_ids == historical_ids
            and overlay is None
            and migration_records in (None, [])
            and audit_catalog_digests.issubset(
                {str(historical_projection["catalog_sha256"])}
            )
            and audit_event_types.issubset({"assign"})
        ):
            validate_program_graph_sources(
                state,
                graph_catalog,
                historical_projection,
            )
            validate_historical_program_provenance(
                state,
                graph_catalog,
                historical_projection,
            )
            return "historical_unbound"
        raise DispatchError(
            "program catalog graph binding is missing; dispatcher recreation is "
            "forbidden and supervisor-signed recovery is required"
        )

    binding = validate_program_graph_binding(binding)
    current_projection = catalog_graph_projection(catalog, catalog_digest)
    if binding["graph_projection"] == current_projection:
        validate_program_graph_sources(state, catalog, current_projection)
        expected_overlay = expected_completion_overlay(
            catalog,
            catalog_digest,
            binding,
        )
        if overlay != expected_overlay:
            raise DispatchError(
                "program completion overlay and catalog graph binding are not exact"
            )
        validate_program_graph_install_audit(
            activity_records,
            catalog,
            catalog_digest,
            binding,
            overlay,
        )
        if catalog.get("overlay_applied") is True:
            if validate_sequencing_epoch_record(
                state, catalog, catalog_digest
            ) is None:
                raise DispatchError(
                    "current sequencing graph is missing its immutable epoch record"
                )
        return "current"
    if source_catalog is not None and source_catalog_digest is not None:
        source_projection = catalog_graph_projection(
            source_catalog,
            source_catalog_digest,
        )
        if binding["graph_projection"] == source_projection:
            validate_program_graph_sources(state, source_catalog, source_projection)
            source_overlay = expected_completion_overlay(
                source_catalog,
                source_catalog_digest,
                binding,
            )
            if overlay != source_overlay:
                raise DispatchError(
                    "base completion overlay and graph binding are not exact"
                )
            validate_program_graph_install_audit(
                activity_records,
                source_catalog,
                source_catalog_digest,
                binding,
                source_overlay,
            )
            return "sequencing_base"
    historical_projection = historical_catalog_graph_projection(
        source_catalog or catalog
    )
    if binding["graph_projection"] == historical_projection:
        raise DispatchError(
            "historical graph binding recreation is forbidden; supervisor-signed "
            "recovery is required"
        )
    raise DispatchError("program catalog graph binding does not match an exact catalog epoch")


def ensure_completion_overlay(
    state: dict[str, Any],
    catalog: dict[str, Any],
    catalog_digest: str,
    timestamp: str,
    *,
    graph_prestate: str,
) -> tuple[list[dict[str, Any]], bool]:
    program_id = str(catalog["program_id"])
    bindings = state.get(PROGRAM_GRAPH_BINDINGS_STATE_KEY)
    if bindings is None:
        bindings = {}
    if not isinstance(bindings, dict):
        raise DispatchError("program catalog graph bindings must be an object")
    current_projection = catalog_graph_projection(catalog, catalog_digest)
    if graph_prestate == "current":
        graph_binding = validate_program_graph_binding(bindings.get(program_id))
    elif graph_prestate == "fresh":
        graph_binding = build_program_graph_binding(
            current_projection,
            bound_at=timestamp,
            binding_reason="dispatcher_initial",
            previous_graph_projection_sha256="0" * 64,
        )
        bindings[program_id] = graph_binding
        state[PROGRAM_GRAPH_BINDINGS_STATE_KEY] = bindings
    elif graph_prestate == "historical_unbound":
        migration = (catalog.get("catalog_migrations") or [None])[0]
        if not isinstance(migration, dict):
            raise DispatchError("historical catalog graph migration is missing")
        graph_binding = build_program_graph_binding(
            current_projection,
            bound_at=timestamp,
            binding_reason=str(migration["id"]),
            previous_graph_projection_sha256=canonical_json_sha256(
                historical_catalog_graph_projection(catalog)
            ),
        )
        bindings[program_id] = graph_binding
        state[PROGRAM_GRAPH_BINDINGS_STATE_KEY] = bindings
    elif graph_prestate == "sequencing_base":
        previous = validate_program_graph_binding(bindings.get(program_id))
        graph_binding = build_program_graph_binding(
            current_projection,
            bound_at=timestamp,
            binding_reason=(
                "sequencing_overlay_v2:"
                + str((catalog.get("release_gate") or {}).get("gate_id") or "")
            ),
            previous_graph_projection_sha256=previous[
                "graph_projection_sha256"
            ],
        )
        bindings[program_id] = graph_binding
        state[PROGRAM_GRAPH_BINDINGS_STATE_KEY] = bindings
    else:
        raise DispatchError("program catalog graph prestate is invalid")
    validate_program_graph_sources(state, catalog, current_projection)

    key = str(catalog["completion_authority"]["live_overlay_state_key"])
    overlays = state.get(key)
    if overlays is None:
        overlays = {}
    if not isinstance(overlays, dict):
        raise DispatchError("program completion authority overlays must be an object")
    expected = expected_completion_overlay(catalog, catalog_digest, graph_binding)
    existing = overlays.get(program_id)
    if existing is not None:
        if graph_prestate == "sequencing_base":
            overlays[program_id] = expected
            state[key] = overlays
        elif existing != expected:
            raise DispatchError("foreign or tampered program completion overlay")
        elif graph_prestate != "current":
            raise DispatchError("program graph transition cannot reuse an existing overlay")
        else:
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
    graph_binding_digest = canonical_json_sha256(graph_binding)
    return [
        {
            "ts": timestamp,
            "agent": str(os.environ.get("AI_NAME") or ""),
            "type": "completion_authority_install",
            "task_id": "LOOP-PROD-SIGNOFF-001",
            "completion_authority_sha256": completion_authority_sha256(catalog),
            "completion_overlay_sha256": overlay_digest,
            "graph_binding_sha256": graph_binding_digest,
            "graph_projection_sha256": graph_binding[
                "graph_projection_sha256"
            ],
            "previous_graph_projection_sha256": graph_binding[
                "previous_graph_projection_sha256"
            ],
            "graph_binding_reason": graph_binding["binding_reason"],
            "message": (
                "Installed exact catalog-bound completion authority and task graph"
            ),
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


def _sequencing_gate_marker(
    catalog: dict[str, Any],
    *,
    parked_at: str,
    previous_status: str,
) -> dict[str, Any]:
    gate = catalog.get("release_gate") or {}
    return {
        "schema_version": SEQUENCING_GATE_MARKER_SCHEMA_VERSION,
        "gate_id": str(gate.get("gate_id") or ""),
        "release_predicate": str(gate.get("release_predicate") or ""),
        "sequencing_overlay_sha256": str(
            catalog.get("sequencing_overlay_sha256") or ""
        ),
        "state": "parked",
        "previous_status": previous_status,
        "parked_at": parked_at,
    }


def _sequencing_epoch_after_task(
    before_task: dict[str, Any] | None,
    effective_task: dict[str, Any],
    catalog: dict[str, Any],
    catalog_digest: str,
    applied_at: str,
    *,
    install_mode: str,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    if install_mode == "base_epoch_migration":
        if not isinstance(before_task, dict):
            raise DispatchError("base sequencing epoch preimage is missing")
        after = deepcopy(before_task)
        created_at = str(before_task.get("created_at") or "")
        for field in TASK_CONTRACT_FIELDS:
            after[field] = deepcopy(effective_task[field])
        expected_runtime = build_task(
            effective_task,
            catalog,
            catalog_digest,
            created_at,
        )
        after["source_ref"] = deepcopy(expected_runtime["source_ref"])
        if "acceptance_deferral" in expected_runtime:
            after["acceptance_deferral"] = deepcopy(
                expected_runtime["acceptance_deferral"]
            )
        else:
            after.pop("acceptance_deferral", None)
    elif install_mode == "fresh_materialization":
        after = build_task(
            effective_task,
            catalog,
            catalog_digest,
            applied_at,
        )
    else:
        raise DispatchError("sequencing epoch install mode is invalid")
    gated_ids = set(
        (catalog.get("release_gate") or {}).get("gated_task_ids") or []
    )
    if str(effective_task["id"]) in gated_ids:
        marker = _sequencing_gate_marker(
            catalog,
            parked_at=applied_at,
            previous_status="todo",
        )
        after["status"] = "blocked"
        after["sequencing_release_gate"] = marker
    else:
        marker = None
        after["status"] = "todo"
        after.pop("sequencing_release_gate", None)
    after["last_update"] = applied_at
    return after, marker


def _sequencing_epoch_activity_records(
    state: dict[str, Any],
    catalog: dict[str, Any],
) -> list[dict[str, Any]]:
    return [
        entry
        for entry in program_activity_records(state, str(catalog["program_id"]))
        if entry.get("type") == "sequencing_overlay_install"
        and entry.get("sequencing_overlay_sha256")
        == catalog.get("sequencing_overlay_sha256")
    ]


def validate_sequencing_epoch_record(
    state: dict[str, Any],
    catalog: dict[str, Any],
    catalog_digest: str,
) -> dict[str, Any] | None:
    records = state.get(PROGRAM_SEQUENCING_EPOCHS_STATE_KEY)
    if records is None:
        return None
    if not isinstance(records, dict):
        raise DispatchError("program sequencing epochs must be an object")
    record = records.get(str(catalog["program_id"]))
    if record is None:
        return None
    required = {
        "schema_version",
        "program_id",
        "source_catalog_sha256",
        "effective_catalog_sha256",
        "sequencing_overlay_sha256",
        "release_gate_id",
        "install_mode",
        "applied_at",
        "source_graph_projection_sha256",
        "effective_graph_projection_sha256",
        "task_count",
        "task_transitions",
        "task_transition_set_sha256",
    }
    transitions = record.get("task_transitions") if isinstance(record, dict) else None
    transition_fields = {
        "task_id",
        "before_task_snapshot",
        "before_task_snapshot_sha256",
        "after_task_snapshot_sha256",
        "before_task_contract_sha256",
        "after_task_contract_sha256",
        "before_source_ref_sha256",
        "after_source_ref_sha256",
        "before_status",
        "after_status",
        "acceptance_deferral_sha256",
        "gate_marker_sha256",
    }
    catalog_tasks = catalog.get("tasks") or []
    by_id = {str(task["id"]): task for task in catalog_tasks}
    source_catalog, source_raw = read_regular_json(
        catalog_path(), label="sequencing epoch source catalog"
    )
    source_digest = sha256_bytes(source_raw)
    source_by_id = {
        str(task["id"]): task for task in source_catalog.get("tasks") or []
    }
    if (
        not isinstance(record, dict)
        or set(record) != required
        or record.get("schema_version") != SEQUENCING_EPOCH_SCHEMA_VERSION
        or record.get("program_id") != catalog.get("program_id")
        or record.get("effective_catalog_sha256") != catalog_digest
        or record.get("source_catalog_sha256") != source_digest
        or source_digest
        != (catalog.get("source_hashes") or {}).get("tasks_catalog_sha256")
        or record.get("sequencing_overlay_sha256")
        != catalog.get("sequencing_overlay_sha256")
        or record.get("release_gate_id")
        != (catalog.get("release_gate") or {}).get("gate_id")
        or record.get("install_mode")
        not in {"base_epoch_migration", "fresh_materialization"}
        or record.get("task_count") != len(catalog_tasks)
        or not isinstance(transitions, list)
        or len(transitions) != len(catalog_tasks)
        or record.get("task_transition_set_sha256")
        != canonical_json_sha256(transitions)
        or record.get("effective_graph_projection_sha256")
        != canonical_json_sha256(catalog_graph_projection(catalog, catalog_digest))
        or record.get("source_graph_projection_sha256")
        != canonical_json_sha256(
            catalog_graph_projection(source_catalog, source_digest)
        )
    ):
        raise DispatchError("program sequencing epoch record is not exact")
    parse_activity_timestamp(record.get("applied_at"))
    expected_ids = [str(task["id"]) for task in catalog_tasks]
    if [str(row.get("task_id") or "") for row in transitions] != expected_ids:
        raise DispatchError("program sequencing epoch transition set is not exact")
    install_mode = str(record["install_mode"])
    applied_at = str(record["applied_at"])
    applied_time = parse_activity_timestamp(applied_at)
    for row in transitions:
        task_id = str(row.get("task_id") or "") if isinstance(row, dict) else ""
        if (
            not isinstance(row, dict)
            or set(row) != transition_fields
            or task_id not in by_id
            or task_id not in source_by_id
        ):
            raise DispatchError("program sequencing epoch transition is not exact")
        preimage = row.get("before_task_snapshot")
        if install_mode == "base_epoch_migration":
            if (
                not isinstance(preimage, dict)
                or preimage.get("id") != task_id
                or preimage.get("status") != "todo"
                or _has_live_admission(preimage)
                or "sequencing_release_gate" in preimage
                or not has_exact_runtime_task_authority(
                    preimage,
                    task_id,
                    source_catalog,
                )
                or task_contract_sha256(preimage)
                != task_contract_sha256(source_by_id[task_id])
            ):
                raise DispatchError("base sequencing epoch preimage is not pristine")
            created_at = preimage.get("created_at")
            last_update = preimage.get("last_update")
            if not isinstance(created_at, str) or not isinstance(last_update, str):
                raise DispatchError("base sequencing epoch preimage timestamps are missing")
            created_time = parse_activity_timestamp(created_at)
            last_update_time = parse_activity_timestamp(last_update)
            if not (created_time <= last_update_time <= applied_time):
                raise DispatchError("base sequencing epoch preimage timestamps are invalid")
            expected_source_runtime = build_task(
                source_by_id[task_id],
                source_catalog,
                source_digest,
                created_at,
            )
            if preimage.get("source_ref") != expected_source_runtime.get("source_ref"):
                raise DispatchError("base sequencing epoch preimage provenance mismatch")
            before_contract_sha256 = task_contract_sha256(preimage)
            before_source_ref_sha256 = canonical_json_sha256(
                preimage.get("source_ref")
            )
            before_status = "todo"
        else:
            if preimage is not None:
                raise DispatchError("fresh sequencing epoch preimage must be null")
            before_contract_sha256 = canonical_json_sha256(None)
            before_source_ref_sha256 = canonical_json_sha256(None)
            before_status = "absent"
        after, marker = _sequencing_epoch_after_task(
            preimage if isinstance(preimage, dict) else None,
            by_id[task_id],
            catalog,
            catalog_digest,
            applied_at,
            install_mode=install_mode,
        )
        expected_transition = {
            "task_id": task_id,
            "before_task_snapshot": deepcopy(preimage),
            "before_task_snapshot_sha256": canonical_json_sha256(preimage),
            "after_task_snapshot_sha256": canonical_json_sha256(after),
            "before_task_contract_sha256": before_contract_sha256,
            "after_task_contract_sha256": task_contract_sha256(after),
            "before_source_ref_sha256": before_source_ref_sha256,
            "after_source_ref_sha256": canonical_json_sha256(
                after.get("source_ref")
            ),
            "before_status": before_status,
            "after_status": str(after["status"]),
            "acceptance_deferral_sha256": canonical_json_sha256(
                after.get("acceptance_deferral")
            ),
            "gate_marker_sha256": canonical_json_sha256(marker),
        }
        if row != expected_transition:
            raise DispatchError("program sequencing epoch transition is not exact")
    audits = _sequencing_epoch_activity_records(state, catalog)
    if len(audits) != 1:
        raise DispatchError(
            "program sequencing epoch requires exactly one durable install audit"
        )
    audit = audits[0]
    if (
        set(audit)
        != ACTIVITY_COMMON_EVENT_FIELDS
        | ACTIVITY_EVENT_EXTRA_FIELDS["sequencing_overlay_install"]
        or audit.get("catalog_sha256") != catalog_digest
        or audit.get("sequencing_epoch_sha256")
        != canonical_json_sha256(record)
        or audit.get("source_catalog_sha256")
        != record["source_catalog_sha256"]
        or audit.get("effective_catalog_sha256") != catalog_digest
        or audit.get("task_transition_set_sha256")
        != record["task_transition_set_sha256"]
        or audit.get("ts") != record["applied_at"]
        or audit.get("task_id")
        != str(catalog["g2_evidence_contract"]["target_task"])
        or audit.get("sequencing_overlay_sha256")
        != record["sequencing_overlay_sha256"]
        or audit.get("message") != "Installed exact sequencing overlay epoch"
    ):
        raise DispatchError("program sequencing epoch audit is not exact")
    return record


def install_sequencing_epoch(
    state: dict[str, Any],
    source_catalog: dict[str, Any],
    source_catalog_digest: str,
    catalog: dict[str, Any],
    catalog_digest: str,
    timestamp: str,
    *,
    graph_prestate: str,
) -> tuple[list[str], list[dict[str, Any]], bool]:
    if catalog.get("overlay_applied") is not True:
        return [], [], False
    existing_record = validate_sequencing_epoch_record(
        state, catalog, catalog_digest
    )
    if graph_prestate == "current":
        return [], [], False
    if graph_prestate == "fresh":
        if existing_record is not None:
            raise DispatchError("fresh sequencing state cannot retain an epoch record")
        return [], [], False
    if graph_prestate != "sequencing_base":
        raise DispatchError(
            "sequencing overlay migration requires a complete bound base-v5 board"
        )
    if existing_record is not None:
        raise DispatchError("base sequencing graph cannot reuse an epoch record")

    active_by_id, archived_by_id = _program_graph_sources(state, source_catalog)
    source_tasks = source_catalog.get("tasks") or []
    expected_ids = [str(task["id"]) for task in source_tasks]
    if set(active_by_id) != set(expected_ids) or archived_by_id:
        raise DispatchError(
            "sequencing epoch migration requires all 48 base tasks active and no archives"
        )
    source_by_id = {str(task["id"]): task for task in source_tasks}
    effective_by_id = {str(task["id"]): task for task in catalog["tasks"]}
    replacements: dict[str, dict[str, Any]] = {}
    transitions: list[dict[str, Any]] = []
    for task_id in expected_ids:
        existing = active_by_id[task_id]
        expected_source_runtime = build_task(
            source_by_id[task_id],
            source_catalog,
            source_catalog_digest,
            str(existing.get("created_at") or timestamp),
        )
        if (
            existing.get("status") != "todo"
            or _has_live_admission(existing)
            or "sequencing_release_gate" in existing
            or not has_exact_runtime_task_authority(
                existing,
                task_id,
                source_catalog,
            )
            or task_contract_sha256(existing)
            != task_contract_sha256(source_by_id[task_id])
            or existing.get("source_ref")
            != expected_source_runtime.get("source_ref")
        ):
            raise DispatchError(
                f"sequencing epoch task {task_id} is not pristine base todo"
            )
        created_at = existing.get("created_at")
        last_update = existing.get("last_update")
        if (
            not isinstance(created_at, str)
            or not isinstance(last_update, str)
            or parse_activity_timestamp(last_update)
            < parse_activity_timestamp(created_at)
            or parse_activity_timestamp(last_update)
            > parse_activity_timestamp(timestamp)
        ):
            raise DispatchError(
                f"sequencing epoch task {task_id} timestamps are invalid"
            )
        before_snapshot = deepcopy(existing)
        after, marker = _sequencing_epoch_after_task(
            before_snapshot,
            effective_by_id[task_id],
            catalog,
            catalog_digest,
            timestamp,
            install_mode="base_epoch_migration",
        )
        transitions.append(
            {
                "task_id": task_id,
                "before_task_snapshot": before_snapshot,
                "before_task_snapshot_sha256": canonical_json_sha256(
                    before_snapshot
                ),
                "after_task_snapshot_sha256": canonical_json_sha256(after),
                "before_task_contract_sha256": task_contract_sha256(
                    before_snapshot
                ),
                "after_task_contract_sha256": task_contract_sha256(after),
                "before_source_ref_sha256": canonical_json_sha256(
                    before_snapshot.get("source_ref")
                ),
                "after_source_ref_sha256": canonical_json_sha256(
                    after.get("source_ref")
                ),
                "before_status": "todo",
                "after_status": str(after["status"]),
                "acceptance_deferral_sha256": canonical_json_sha256(
                    after.get("acceptance_deferral")
                ),
                "gate_marker_sha256": canonical_json_sha256(marker),
            }
        )
        replacements[task_id] = after

    source_projection = catalog_graph_projection(
        source_catalog, source_catalog_digest
    )
    effective_projection = catalog_graph_projection(catalog, catalog_digest)
    record = {
        "schema_version": SEQUENCING_EPOCH_SCHEMA_VERSION,
        "program_id": str(catalog["program_id"]),
        "source_catalog_sha256": source_catalog_digest,
        "effective_catalog_sha256": catalog_digest,
        "sequencing_overlay_sha256": str(catalog["sequencing_overlay_sha256"]),
        "release_gate_id": str(catalog["release_gate"]["gate_id"]),
        "install_mode": "base_epoch_migration",
        "applied_at": timestamp,
        "source_graph_projection_sha256": canonical_json_sha256(source_projection),
        "effective_graph_projection_sha256": canonical_json_sha256(
            effective_projection
        ),
        "task_count": len(transitions),
        "task_transitions": transitions,
        "task_transition_set_sha256": canonical_json_sha256(transitions),
    }
    state["tasks"] = [
        replacements.get(str(task.get("id") or ""), task)
        for task in state["tasks"]
    ]
    epoch_records = state.get(PROGRAM_SEQUENCING_EPOCHS_STATE_KEY) or {}
    if not isinstance(epoch_records, dict):
        raise DispatchError("program sequencing epochs must be an object")
    epoch_records[str(catalog["program_id"])] = record
    state[PROGRAM_SEQUENCING_EPOCHS_STATE_KEY] = epoch_records
    state["updated_at"] = timestamp
    log = {
        "ts": timestamp,
        "agent": str(os.environ.get("AI_NAME") or ""),
        "type": "sequencing_overlay_install",
        "task_id": str(catalog["g2_evidence_contract"]["target_task"]),
        "sequencing_epoch_sha256": canonical_json_sha256(record),
        "sequencing_overlay_sha256": str(catalog["sequencing_overlay_sha256"]),
        "source_catalog_sha256": source_catalog_digest,
        "effective_catalog_sha256": catalog_digest,
        "task_transition_set_sha256": record["task_transition_set_sha256"],
        "message": "Installed exact sequencing overlay epoch",
    }
    return expected_ids, [log], True


def install_fresh_sequencing_epoch(
    state: dict[str, Any],
    source_catalog: dict[str, Any],
    source_catalog_digest: str,
    catalog: dict[str, Any],
    catalog_digest: str,
    timestamp: str,
    *,
    graph_prestate: str,
) -> tuple[list[dict[str, Any]], bool]:
    if catalog.get("overlay_applied") is not True or graph_prestate != "fresh":
        return [], False
    if validate_sequencing_epoch_record(state, catalog, catalog_digest) is not None:
        raise DispatchError("fresh sequencing materialization cannot reuse an epoch")
    if validate_sequencing_release_record(state, catalog, catalog_digest) is not None:
        raise DispatchError("fresh sequencing materialization cannot be pre-released")
    active_by_id, archived_by_id = _program_graph_sources(state, catalog)
    effective_tasks = catalog.get("tasks") or []
    expected_ids = [str(task["id"]) for task in effective_tasks]
    if set(active_by_id) != set(expected_ids) or archived_by_id:
        raise DispatchError(
            "fresh sequencing materialization requires all 48 tasks active"
        )
    effective_by_id = {str(task["id"]): task for task in effective_tasks}
    gated_ids = set((catalog.get("release_gate") or {}).get("gated_task_ids") or [])
    transitions: list[dict[str, Any]] = []
    for task_id in expected_ids:
        task = active_by_id[task_id]
        validate_existing_task_provenance(
            task,
            effective_by_id[task_id],
            catalog,
            catalog_digest,
            source="fresh active",
        )
        expected_status = "blocked" if task_id in gated_ids else "todo"
        expected_marker = None
        if task_id in gated_ids:
            expected_marker = _validate_sequencing_gate_marker(
                task.get("sequencing_release_gate"), catalog
            )
        elif "sequencing_release_gate" in task:
            raise DispatchError("ungated fresh task carries a sequencing marker")
        if task.get("status") != expected_status or task.get("last_update") != timestamp:
            raise DispatchError("fresh sequencing task state is not exact")
        expected_after, expected_marker = _sequencing_epoch_after_task(
            None,
            effective_by_id[task_id],
            catalog,
            catalog_digest,
            timestamp,
            install_mode="fresh_materialization",
        )
        if task != expected_after:
            raise DispatchError("fresh sequencing task snapshot is not exact")
        transitions.append(
            {
                "task_id": task_id,
                "before_task_snapshot": None,
                "before_task_snapshot_sha256": canonical_json_sha256(None),
                "after_task_snapshot_sha256": canonical_json_sha256(
                    expected_after
                ),
                "before_task_contract_sha256": canonical_json_sha256(None),
                "after_task_contract_sha256": task_contract_sha256(task),
                "before_source_ref_sha256": canonical_json_sha256(None),
                "after_source_ref_sha256": canonical_json_sha256(
                    task.get("source_ref")
                ),
                "before_status": "absent",
                "after_status": expected_status,
                "acceptance_deferral_sha256": canonical_json_sha256(
                    task.get("acceptance_deferral")
                ),
                "gate_marker_sha256": canonical_json_sha256(expected_marker),
            }
        )
    record = {
        "schema_version": SEQUENCING_EPOCH_SCHEMA_VERSION,
        "program_id": str(catalog["program_id"]),
        "source_catalog_sha256": source_catalog_digest,
        "effective_catalog_sha256": catalog_digest,
        "sequencing_overlay_sha256": str(catalog["sequencing_overlay_sha256"]),
        "release_gate_id": str(catalog["release_gate"]["gate_id"]),
        "install_mode": "fresh_materialization",
        "applied_at": timestamp,
        "source_graph_projection_sha256": canonical_json_sha256(
            catalog_graph_projection(source_catalog, source_catalog_digest)
        ),
        "effective_graph_projection_sha256": canonical_json_sha256(
            catalog_graph_projection(catalog, catalog_digest)
        ),
        "task_count": len(transitions),
        "task_transitions": transitions,
        "task_transition_set_sha256": canonical_json_sha256(transitions),
    }
    records = state.get(PROGRAM_SEQUENCING_EPOCHS_STATE_KEY) or {}
    if not isinstance(records, dict):
        raise DispatchError("program sequencing epochs must be an object")
    records[str(catalog["program_id"])] = record
    state[PROGRAM_SEQUENCING_EPOCHS_STATE_KEY] = records
    state["updated_at"] = timestamp
    return [
        {
            "ts": timestamp,
            "agent": str(os.environ.get("AI_NAME") or ""),
            "type": "sequencing_overlay_install",
            "task_id": str(catalog["g2_evidence_contract"]["target_task"]),
            "sequencing_epoch_sha256": canonical_json_sha256(record),
            "sequencing_overlay_sha256": str(catalog["sequencing_overlay_sha256"]),
            "source_catalog_sha256": source_catalog_digest,
            "effective_catalog_sha256": catalog_digest,
            "task_transition_set_sha256": record["task_transition_set_sha256"],
            "message": "Installed exact sequencing overlay epoch",
        }
    ], True


def _validate_sequencing_gate_marker(
    marker: Any,
    catalog: dict[str, Any],
) -> dict[str, Any]:
    required = {
        "schema_version",
        "gate_id",
        "release_predicate",
        "sequencing_overlay_sha256",
        "state",
        "previous_status",
        "parked_at",
    }
    gate = catalog.get("release_gate") or {}
    if (
        not isinstance(marker, dict)
        or set(marker) != required
        or marker.get("schema_version") != SEQUENCING_GATE_MARKER_SCHEMA_VERSION
        or marker.get("gate_id") != gate.get("gate_id")
        or marker.get("release_predicate") != gate.get("release_predicate")
        or marker.get("sequencing_overlay_sha256")
        != catalog.get("sequencing_overlay_sha256")
        or marker.get("state") != "parked"
        or marker.get("previous_status") != "todo"
    ):
        raise DispatchError("sequencing release gate marker is not exact")
    parse_activity_timestamp(marker.get("parked_at"))
    return marker


def _release_activity_records(
    state: dict[str, Any],
    catalog: dict[str, Any],
) -> list[dict[str, Any]]:
    return [
        entry
        for entry in program_activity_records(state, str(catalog["program_id"]))
        if entry.get("type") == "sequencing_gate_release"
        and entry.get("sequencing_overlay_sha256")
        == catalog.get("sequencing_overlay_sha256")
    ]


def validate_sequencing_release_record(
    state: dict[str, Any],
    catalog: dict[str, Any],
    catalog_digest: str,
) -> dict[str, Any] | None:
    records = state.get(PROGRAM_SEQUENCING_RELEASES_STATE_KEY)
    if records is None:
        return None
    if not isinstance(records, dict):
        raise DispatchError("program sequencing releases must be an object")
    record = records.get(str(catalog["program_id"]))
    if record is None:
        return None
    required = {
        "schema_version",
        "program_id",
        "effective_catalog_sha256",
        "sequencing_overlay_sha256",
        "release_gate_id",
        "release_predicate",
        "sequencing_epoch_sha256",
        "released_at",
        "g2_issued_at",
        "closeout_at",
        "g2_evidence_sha256",
        "canonical_record_bundle_sha256",
        "canonical_source_snapshot_sha256",
        "canonical_source_attestation",
        "hosted_probe_sha256",
        "g2_artifact_commit_sha",
        "g2_artifact_merge_target_sha",
        "g2_authoritative_remote_head_sha",
        "g2_github_pr_snapshot_sha256",
        "product_manifest_sha256",
        "product_manifest_sidecar_sha256",
        "target_task_snapshot_sha256",
        "reviewer",
        "review_binding_sha256",
        "review_approval_event_sha256",
        "review_verdict_sha256",
        "release_admission_sha256",
        "released_task_transitions",
        "released_task_transition_set_sha256",
    }
    transitions = (
        record.get("released_task_transitions")
        if isinstance(record, dict)
        else None
    )
    transition_fields = {
        "task_id",
        "before_task_snapshot_sha256",
        "after_task_snapshot_sha256",
        "before_status",
        "after_status",
    }
    gate = catalog.get("release_gate") or {}
    if (
        not isinstance(record, dict)
        or set(record) != required
        or record.get("schema_version") != 2
        or record.get("program_id") != catalog.get("program_id")
        or record.get("effective_catalog_sha256") != catalog_digest
        or record.get("sequencing_overlay_sha256")
        != catalog.get("sequencing_overlay_sha256")
        or record.get("release_gate_id") != gate.get("gate_id")
        or record.get("release_predicate") != gate.get("release_predicate")
        or not isinstance(transitions, list)
        or record.get("released_task_transition_set_sha256")
        != canonical_json_sha256(transitions)
    ):
        raise DispatchError("program sequencing release record is not exact")
    parse_activity_timestamp(record.get("released_at"))
    g2_issued_at = parse_activity_timestamp(record.get("g2_issued_at"))
    closeout_at = parse_activity_timestamp(record.get("closeout_at"))
    released_at = parse_activity_timestamp(record.get("released_at"))
    if closeout_at > g2_issued_at or g2_issued_at > released_at:
        raise DispatchError("program sequencing release chronology is invalid")
    epoch = validate_sequencing_epoch_record(state, catalog, catalog_digest)
    if epoch is None or parse_activity_timestamp(epoch.get("applied_at")) > released_at:
        raise DispatchError(
            "program sequencing release is missing its preceding epoch"
        )
    if record.get("sequencing_epoch_sha256") != canonical_json_sha256(epoch):
        raise DispatchError("program sequencing release epoch binding is invalid")
    epoch_transitions = {
        str(row.get("task_id") or ""): row
        for row in epoch.get("task_transitions") or []
        if isinstance(row, dict)
    }
    gated_ids = set(gate.get("gated_task_ids") or [])
    expected_transition_ids = [
        str(task["id"])
        for task in catalog.get("tasks") or []
        if str(task["id"]) in gated_ids
    ]
    transition_ids: list[str] = []
    for transition in transitions:
        task_id = str(transition.get("task_id") or "") if isinstance(transition, dict) else ""
        if (
            not isinstance(transition, dict)
            or set(transition) != transition_fields
            or task_id not in gated_ids
            or transition.get("before_status") != "blocked"
            or transition.get("after_status") != "todo"
            or task_id not in epoch_transitions
            or transition.get("before_task_snapshot_sha256")
            != epoch_transitions[task_id].get("after_task_snapshot_sha256")
            or any(
                not _is_lower_hex(transition.get(field), 64)
                for field in (
                    "before_task_snapshot_sha256",
                    "after_task_snapshot_sha256",
                )
            )
        ):
            raise DispatchError("program sequencing release transition is not exact")
        transition_ids.append(task_id)
    if (
        len(transition_ids) != len(set(transition_ids))
        or transition_ids != expected_transition_ids
    ):
        raise DispatchError("program sequencing release transition set is not exact")

    # G2 freshness and canonical artifact resolution are admission-time checks.
    # Once that exact byte decision is committed, the release is a one-way,
    # content-addressed fact: recovery must not depend on mutable artifact paths
    # or on a later rendering of the closeout task.  Explicit revocation, if it
    # is ever needed, requires its own governed state transition.
    admission = {
        field: deepcopy(record.get(field))
        for field in SEQUENCING_RELEASE_ADMISSION_FIELDS
    }
    digest_fields = SEQUENCING_RELEASE_ADMISSION_FIELDS - {
        "reviewer",
        "g2_issued_at",
        "closeout_at",
        "g2_artifact_commit_sha",
        "g2_artifact_merge_target_sha",
        "g2_authoritative_remote_head_sha",
        "canonical_source_attestation",
    }
    attestation = record.get("canonical_source_attestation")
    contract = catalog.get("g2_evidence_contract") or {}
    reviewer = record.get("reviewer")
    if (
        any(not _is_lower_hex(record.get(field), 64) for field in digest_fields)
        or not _is_lower_hex(record.get("g2_artifact_commit_sha"), 40)
        or not _is_lower_hex(
            record.get("g2_artifact_merge_target_sha"), 40
        )
        or not _is_lower_hex(record.get("g2_authoritative_remote_head_sha"), 40)
        or not _is_lower_hex(record.get("sequencing_epoch_sha256"), 64)
        or not isinstance(attestation, dict)
        or set(attestation) != G2_SOURCE_ATTESTATION_KEYS
        or attestation.get("database") != contract.get("canonical_database_name")
        or attestation.get("role") != contract.get("canonical_database_role")
        or attestation.get("schema") != contract.get("canonical_database_schema")
        or attestation.get("table") != contract.get("canonical_database_table")
        or attestation.get("projection_root")
        != contract.get("canonical_projection_root")
        or type(attestation.get("live_source_high_watermark")) is not int
        or type(attestation.get("current_projection_checkpoint")) is not int
        or attestation["current_projection_checkpoint"]
        < attestation["live_source_high_watermark"]
        or not _is_lower_hex(attestation.get("rows_sha256"), 64)
        or not _is_lower_hex(attestation.get("projection_sha256"), 64)
        or not isinstance(attestation.get("captured_generation_name"), str)
        or not isinstance(attestation.get("current_generation_name"), str)
        or re.fullmatch(
            r"g[0-9]{12}-[0-9a-f]{12}",
            str(attestation.get("captured_generation_name") or ""),
        )
        is None
        or re.fullmatch(
            r"g[0-9]{12}-[0-9a-f]{12}",
            str(attestation.get("current_generation_name") or ""),
        )
        is None
        or not isinstance(reviewer, str)
        or not reviewer.strip()
        or reviewer not in set(catalog.get("allowed_owners") or [])
        or not _is_lower_hex(record.get("release_admission_sha256"), 64)
        or record.get("release_admission_sha256")
        != canonical_json_sha256(admission)
    ):
        raise DispatchError("persisted G2 release admission snapshot is not exact")
    audits = _release_activity_records(state, catalog)
    if len(audits) != 1:
        raise DispatchError(
            "program sequencing release requires exactly one durable audit"
        )
    audit = audits[0]
    if (
        set(audit)
        != ACTIVITY_COMMON_EVENT_FIELDS
        | ACTIVITY_EVENT_EXTRA_FIELDS["sequencing_gate_release"]
        or audit.get("catalog_sha256") != catalog_digest
        or audit.get("release_gate_id") != gate.get("gate_id")
        or audit.get("release_record_sha256") != canonical_json_sha256(record)
        or audit.get("released_task_transition_set_sha256")
        != record["released_task_transition_set_sha256"]
        or audit.get("ts") != record["released_at"]
        or audit.get("task_id")
        != str(catalog["g2_evidence_contract"]["target_task"])
        or audit.get("sequencing_overlay_sha256")
        != record["sequencing_overlay_sha256"]
        or audit.get("message")
        != "Released exact sequencing gate after G2 admission"
    ):
        raise DispatchError("program sequencing release audit is not exact")
    return record


def _build_sequencing_release_record(
    catalog: dict[str, Any],
    catalog_digest: str,
    timestamp: str,
    transitions: list[dict[str, Any]],
    admission: Mapping[str, Any],
    sequencing_epoch_sha256: str,
) -> dict[str, Any]:
    gate = catalog["release_gate"]
    if set(admission) != SEQUENCING_RELEASE_ADMISSION_FIELDS:
        raise DispatchError("G2 release admission snapshot is not exact")
    gated_ids = set((catalog.get("release_gate") or {}).get("gated_task_ids") or [])
    if (
        len(transitions) != len(gated_ids)
        or {str(row.get("task_id") or "") for row in transitions} != gated_ids
    ):
        raise DispatchError("G2 release did not transition the exact gated task set")
    released_at = parse_activity_timestamp(timestamp)
    if parse_activity_timestamp(admission["g2_issued_at"]) > released_at:
        raise DispatchError("G2 release predates its admitted evidence")
    return {
        "schema_version": 2,
        "program_id": str(catalog["program_id"]),
        "effective_catalog_sha256": catalog_digest,
        "sequencing_overlay_sha256": str(catalog["sequencing_overlay_sha256"]),
        "release_gate_id": str(gate["gate_id"]),
        "release_predicate": str(gate["release_predicate"]),
        "sequencing_epoch_sha256": sequencing_epoch_sha256,
        "released_at": timestamp,
        "release_admission_sha256": canonical_json_sha256(dict(admission)),
        **{
            field: deepcopy(admission[field])
            for field in sorted(SEQUENCING_RELEASE_ADMISSION_FIELDS)
        },
        "released_task_transitions": transitions,
        "released_task_transition_set_sha256": canonical_json_sha256(transitions),
    }


def materialize(
    state: dict[str, Any],
    tasks: list[dict[str, Any]],
    catalog: dict[str, Any],
    catalog_digest: str,
    timestamp: str,
) -> tuple[list[str], list[str], list[str], list[dict[str, Any]], bool]:
    active_tasks = state.setdefault("tasks", [])
    if not isinstance(active_tasks, list) or any(
        not isinstance(task, dict) for task in active_tasks
    ):
        raise DispatchError("materialization requires exact active task objects")
    active_ids = [str(task.get("id") or "") for task in active_tasks]
    if any(not task_id for task_id in active_ids) or len(active_ids) != len(
        set(active_ids)
    ):
        raise DispatchError("active task identities are missing or duplicated")
    active_by_id = {str(task["id"]): task for task in active_tasks}
    if catalog.get("overlay_applied") is True:
        task_ids = [str(task.get("id") or "") for task in tasks]
        expected_task_ids = [str(task["id"]) for task in catalog.get("tasks") or []]
        if task_ids != expected_task_ids:
            raise DispatchError(
                "sequencing materialization requires the complete ordered catalog"
            )
    created: list[str] = []
    preserved: list[str] = []
    archived: list[str] = []
    logs: list[dict[str, Any]] = []
    additive_ids = {str(item) for item in catalog.get("additive_task_ids") or []}
    release_gate = catalog.get("release_gate") if catalog.get("overlay_applied") else None
    gated_ids: set[str] = set()
    gate_open = True
    release_admission: dict[str, Any] | None = None
    persisted_release: dict[str, Any] | None = None
    if release_gate is not None:
        if not isinstance(release_gate, dict):
            raise DispatchError("sequencing release gate is malformed")
        gated_ids = set(release_gate.get("gated_task_ids") or [])
        persisted_release = validate_sequencing_release_record(
            state, catalog, catalog_digest
        )
        gate_open = persisted_release is not None
        if not gate_open:
            release_admission = resolve_g2_evidence_admission(state, catalog)
            gate_open = release_admission is not None
    release_admission_sha256 = (
        canonical_json_sha256(release_admission)
        if release_admission is not None
        else None
    )
    pending_materialized: list[dict[str, Any]] = []
    released_transitions: list[dict[str, Any]] = []

    for task in tasks:
        task_id = task["id"]
        archive_payload = read_canonical_archive_payload(
            ARCHIVE_ROOT / f"{task_id}.json"
        )
        archive_state: str | None = None
        archived_task: dict[str, Any] | None = None
        if archive_payload is not None:
            archive_state = str(archive_payload.get("terminal_status") or "")
            archived_task = archive_payload.get("task")
            if (
                archive_payload.get("task_id") != task_id
                or archive_state not in TERMINAL_STATUSES
                or not isinstance(archived_task, dict)
                or archived_task.get("id") != task_id
                or archived_task.get("status") != archive_state
            ):
                raise DispatchError(
                    f"archive collision for {task_id} is not an exact terminal record"
                )
        existing = active_by_id.get(task_id)
        if existing is not None and archive_payload is not None:
            raise DispatchError(
                f"catalog task {task_id} exists in both active and archive state"
            )

        if task_id in gated_ids and not gate_open:
            if archive_payload is not None:
                raise DispatchError(
                    f"gated task {task_id} reached terminal archive before valid G2"
                )
            if existing is not None:
                if existing.get("status") != "blocked":
                    raise DispatchError(
                        f"gated task {task_id} is not durably parked before valid G2"
                    )
                validate_existing_task_provenance(
                    existing,
                    task,
                    catalog,
                    catalog_digest,
                    source="active",
                )
                _validate_sequencing_gate_marker(
                    existing.get("sequencing_release_gate"), catalog
                )
                preserved.append(f"{task_id}:g2-gated-blocked")
                continue

        if task_id in gated_ids and gate_open and persisted_release is None:
            if archive_payload is not None:
                raise DispatchError(
                    f"gated task {task_id} reached archive before first G2 release"
                )
            if existing is not None:
                if existing.get("status") != "blocked" or _has_live_admission(existing):
                    raise DispatchError(
                        f"gated task {task_id} bypassed its pre-G2 park"
                    )
                _validate_sequencing_gate_marker(
                    existing.get("sequencing_release_gate"), catalog
                )

        if archive_payload is not None:
            if task_id in additive_ids:
                if catalog.get("overlay_applied") is True:
                    validate_existing_task_provenance(
                        archived_task,
                        task,
                        catalog,
                        catalog_digest,
                        source="archive",
                    )
                validate_additive_collision(
                    archived_task,
                    task,
                    catalog,
                    catalog_digest,
                    source="archive",
                )
            else:
                validate_existing_task_provenance(
                    archived_task,
                    task,
                    catalog,
                    catalog_digest,
                    source="archive",
                )
            archived.append(f"{task_id}:{archive_state}")
            if (
                task_id in gated_ids
                and persisted_release is not None
                and archived_task.get("sequencing_release_admission_sha256")
                != persisted_release.get("release_admission_sha256")
            ):
                raise DispatchError(
                    f"released gated archive {task_id} lost its release admission"
                )
            continue

        if existing is not None:
            if task_id in additive_ids:
                if catalog.get("overlay_applied") is True:
                    validate_existing_task_provenance(
                        existing,
                        task,
                        catalog,
                        catalog_digest,
                        source="active",
                    )
                validate_additive_collision(
                    existing,
                    task,
                    catalog,
                    catalog_digest,
                    source="active",
                )
            else:
                validate_existing_task_provenance(
                    existing,
                    task,
                    catalog,
                    catalog_digest,
                    source="active",
                )
            if (
                task_id in gated_ids
                and gate_open
                and persisted_release is None
                and "sequencing_release_gate" in existing
            ):
                marker = _validate_sequencing_gate_marker(
                    existing.get("sequencing_release_gate"), catalog
                )
                before = deepcopy(existing)
                existing["status"] = str(marker["previous_status"])
                existing.pop("sequencing_release_gate", None)
                existing["sequencing_release_admission_sha256"] = (
                    release_admission_sha256
                )
                existing["last_update"] = timestamp
                released_transitions.append(
                    {
                        "task_id": task_id,
                        "before_task_snapshot_sha256": canonical_json_sha256(before),
                        "after_task_snapshot_sha256": canonical_json_sha256(existing),
                        "before_status": "blocked",
                        "after_status": str(existing["status"]),
                    }
                )
                preserved.append(f"{task_id}:g2-released-{existing['status']}")
            else:
                if task_id in gated_ids and "sequencing_release_gate" in existing:
                    raise DispatchError(
                        f"released gated task {task_id} regained a park marker"
                    )
                if (
                    task_id in gated_ids
                    and persisted_release is not None
                    and existing.get("sequencing_release_admission_sha256")
                    != persisted_release.get("release_admission_sha256")
                ):
                    raise DispatchError(
                        f"released gated task {task_id} lost its release admission"
                    )
                preserved.append(f"{task_id}:{existing.get('status', 'unknown')}")
            continue

        materialized = build_task(task, catalog, catalog_digest, timestamp)
        if task_id in gated_ids and not gate_open:
            materialized["status"] = "blocked"
            materialized["sequencing_release_gate"] = _sequencing_gate_marker(
                catalog,
                parked_at=timestamp,
                previous_status="todo",
            )
        elif task_id in gated_ids and gate_open and persisted_release is None:
            raise DispatchError(
                f"first G2 release requires a durably parked task: {task_id}"
            )
        pending_materialized.append(materialized)
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

    active_tasks.extend(pending_materialized)
    if release_gate is not None and gate_open and persisted_release is None:
        release_epoch = validate_sequencing_epoch_record(
            state,
            catalog,
            catalog_digest,
        )
        if release_epoch is None:
            raise DispatchError("G2 release requires an exact sequencing epoch")
        release_record = _build_sequencing_release_record(
            catalog,
            catalog_digest,
            timestamp,
            released_transitions,
            release_admission or {},
            canonical_json_sha256(release_epoch),
        )
        release_records = state.get(PROGRAM_SEQUENCING_RELEASES_STATE_KEY) or {}
        if not isinstance(release_records, dict):
            raise DispatchError("program sequencing releases must be an object")
        release_records[str(catalog["program_id"])] = release_record
        state[PROGRAM_SEQUENCING_RELEASES_STATE_KEY] = release_records
        logs.append(
            {
                "ts": timestamp,
                "agent": str(os.environ.get("AI_NAME") or ""),
                "type": "sequencing_gate_release",
                "task_id": str(catalog["g2_evidence_contract"]["target_task"]),
                "release_gate_id": str(release_gate["gate_id"]),
                "sequencing_overlay_sha256": str(
                    catalog["sequencing_overlay_sha256"]
                ),
                "release_record_sha256": canonical_json_sha256(release_record),
                "released_task_transition_set_sha256": release_record[
                    "released_task_transition_set_sha256"
                ],
                "message": "Released exact sequencing gate after G2 admission",
            }
        )
    state_changed = bool(
        pending_materialized
        or released_transitions
        or (release_gate is not None and gate_open and persisted_release is None)
    )
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
    parser.add_argument(
        "--sequencing-overlay",
        type=str,
        default="",
        help="Path to an optional sequencing overlay JSON file to apply to the catalog.",
    )
    return parser.parse_args()


def _parse_g2_timestamp(value: Any, *, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise DispatchError(f"{label} must be a UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DispatchError(f"{label} must be a UTC timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise DispatchError(f"{label} must be timezone-aware UTC")
    return parsed.astimezone(timezone.utc)


def _read_g2_artifact(
    contract: Mapping[str, Any],
    path_key: str,
    *,
    label: str,
) -> tuple[dict[str, Any], bytes, Path]:
    relative = _safe_repo_relative_path(
        contract.get(path_key), label=f"{label} path"
    )
    payload, raw = read_rooted_regular_json(
        REPO_ROOT, relative, label=label
    )
    path = REPO_ROOT / relative
    return payload, raw, path


def _resolve_g2_closeout_task(
    state: dict[str, Any],
    catalog: dict[str, Any],
    contract: Mapping[str, Any],
) -> tuple[dict[str, Any], datetime]:
    target_id = str(contract["target_task"])
    active = state.get("tasks")
    if not isinstance(active, list) or any(not isinstance(item, dict) for item in active):
        raise DispatchError("G2 closeout requires exact active task objects")
    matches = [item for item in active if item.get("id") == target_id]
    if len(matches) > 1:
        raise DispatchError("G2 target is duplicated in active state")
    archive_path = ARCHIVE_ROOT / f"{target_id}.json"
    archive = read_canonical_archive_payload(archive_path)
    if matches and archive is not None:
        raise DispatchError("G2 target exists in both active and archive state")
    if archive is not None:
        expected_archive_keys = {
            "version",
            "task_id",
            "archived_at",
            "terminal_status",
            "terminal_outcome",
            "task",
            "handoffs",
            "blockers",
        }
        task = archive.get("task")
        if (
            set(archive) != expected_archive_keys
            or archive.get("version") != 1
            or archive.get("task_id") != target_id
            or archive.get("terminal_status") != "done"
            or archive.get("terminal_outcome") != "completed"
            or not isinstance(archive.get("handoffs"), list)
            or not isinstance(archive.get("blockers"), list)
            or not isinstance(task, dict)
        ):
            raise DispatchError("G2 target archive closeout is not exact")
        closeout_at = _parse_g2_timestamp(
            archive.get("archived_at"), label="G2 archived_at"
        )
    elif matches:
        task = matches[0]
        closeout_at = _parse_g2_timestamp(
            task.get("last_update"), label="G2 active closeout last_update"
        )
    else:
        raise DispatchError("G2 target has no accepted closeout task")
    if (
        task.get("id") != target_id
        or task.get("status") != "done"
        or task.get("terminal_outcome") != "completed"
    ):
        raise DispatchError("G2 target is not done with completed outcome")
    owner = str(task.get("owner") or "").strip()
    reviewer = str(task.get("reviewer") or "").strip()
    if not owner or not reviewer or owner == reviewer:
        raise DispatchError("G2 closeout owner and reviewer must be distinct")
    if task.get("review_file") != contract.get("closeout_manifest_path"):
        raise DispatchError("G2 closeout review_file mismatch")
    review_notes = task.get("review_notes_zh", task.get("review_notes"))
    if not (
        isinstance(review_notes, str)
        and review_notes.strip()
        or isinstance(review_notes, list)
        and review_notes
        and all(isinstance(item, str) and item.strip() for item in review_notes)
    ):
        raise DispatchError("G2 closeout requires non-empty review notes")
    delivery = task.get("delivery")
    if (
        not isinstance(delivery, dict)
        or delivery.get("head_merged_to_target") is not True
        or delivery.get("push_status") != "in_sync"
        or delivery.get("merge_target_branch") != "dev"
        or not _is_lower_hex(delivery.get("commit"), 40)
        or not _is_lower_hex(delivery.get("merge_target_sha"), 40)
    ):
        raise DispatchError("G2 closeout delivery truth is not accepted")
    source_ref = task.get("source_ref")
    source_hashes = catalog.get("source_hashes") or {}
    target_spec = next(
        (
            item
            for item in catalog.get("tasks") or []
            if item.get("id") == target_id
        ),
        None,
    )
    if not isinstance(target_spec, dict):
        raise DispatchError("G2 target specification is missing")
    expected_runtime = build_task(
        target_spec,
        catalog,
        canonical_json_sha256(catalog),
        str(task.get("created_at") or task.get("last_update") or iso_now()),
    )
    expected_source = {
        "program_id": catalog.get("program_id"),
        "catalog_sha256": canonical_json_sha256(catalog),
        "source_catalog_sha256": source_hashes.get("tasks_catalog_sha256"),
        "sequencing_addendum_sha256": source_hashes.get(
            "sequencing_addendum_sha256"
        ),
        "merge_pr_3737_sha": source_hashes.get("merge_pr_3737_sha"),
        "sequencing_overlay_sha256": catalog.get("sequencing_overlay_sha256"),
        "original_task_contract_sha256": contract.get(
            "target_task_original_contract_sha256"
        ),
        "amended_task_contract_sha256": contract.get(
            "target_task_amended_contract_sha256"
        ),
        "task_contract_sha256": contract.get(
            "target_task_amended_contract_sha256"
        ),
        "g2_release_checkpoint": True,
        "acceptance_sha256": expected_runtime["source_ref"][
            "acceptance_sha256"
        ],
        "acceptance_deferral_sha256": expected_runtime["source_ref"][
            "acceptance_deferral_sha256"
        ],
    }
    if not isinstance(source_ref, dict) or any(
        source_ref.get(key) != expected for key, expected in expected_source.items()
    ):
        raise DispatchError("G2 closeout source provenance mismatch")
    if task_contract_sha256(task) != contract.get(
        "target_task_amended_contract_sha256"
    ):
        raise DispatchError("G2 closeout task contract mismatch")
    if task.get("acceptance_deferral") != expected_runtime.get(
        "acceptance_deferral"
    ):
        raise DispatchError("G2 closeout acceptance deferral mismatch")
    return task, closeout_at


def _g2_closeout_task_projection(task: Mapping[str, Any]) -> dict[str, Any]:
    """Bind reviewed closeout truth without a self-referential Git SHA."""

    review_notes = task.get("review_notes_zh", task.get("review_notes"))
    return {
        "id": task.get("id"),
        "status": task.get("status"),
        "terminal_outcome": task.get("terminal_outcome"),
        "owner": task.get("owner"),
        "reviewer": task.get("reviewer"),
        "review_file": task.get("review_file"),
        "review_notes": deepcopy(review_notes),
        "acceptance": deepcopy(task.get("acceptance")),
        "source_ref": deepcopy(task.get("source_ref")),
        "acceptance_deferral": deepcopy(task.get("acceptance_deferral")),
        "task_contract_sha256": task_contract_sha256(dict(task)),
    }


def _validate_g2_implementation_git_delivery(
    pull_request: Mapping[str, Any],
    closeout_task: Mapping[str, Any],
    validated_base_sha: str,
) -> None:
    head_sha = str(pull_request.get("head_sha") or "")
    merge_sha = str(pull_request.get("merge_sha") or "")
    evidence_head_sha = str((closeout_task.get("delivery") or {}).get("commit") or "")
    if not all(
        _is_lower_hex(value, 40)
        for value in (
            head_sha,
            merge_sha,
            evidence_head_sha,
            validated_base_sha,
        )
    ):
        raise DispatchError("G2 implementation Git delivery identity is invalid")
    _git_output(REPO_ROOT, "cat-file", "-e", f"{head_sha}^{{commit}}")
    _git_output(REPO_ROOT, "cat-file", "-e", f"{merge_sha}^{{commit}}")
    parents = (
        _git_output(REPO_ROOT, "show", "-s", "--format=%P", merge_sha)
        .decode("utf-8")
        .strip()
        .split()
    )
    if len(parents) != 2 or parents != [validated_base_sha, head_sha]:
        raise DispatchError("G2 implementation merge does not contain its exact head")
    _git_output(
        REPO_ROOT,
        "merge-base",
        "--is-ancestor",
        merge_sha,
        evidence_head_sha,
    )


def _validate_g2_committed_artifacts(
    closeout_task: Mapping[str, Any],
    contract: Mapping[str, Any],
    artifact_bytes: Mapping[str, bytes],
) -> dict[str, str]:
    delivery = closeout_task.get("delivery")
    if not isinstance(delivery, Mapping):
        raise DispatchError("G2 artifact delivery metadata is missing")
    artifact_commit = str(delivery.get("commit") or "")
    merge_target = str(delivery.get("merge_target_sha") or "")
    required_url = str(contract.get("required_git_remote_url") or "")
    required_ref = str(contract.get("required_git_remote_ref") or "")
    if (
        contract.get("artifact_commit_binding")
        != "reviewer_and_github_bound_git_tree_v2"
        or not _is_lower_hex(artifact_commit, 40)
        or not _is_lower_hex(merge_target, 40)
        or required_url != "https://github.com/ajoe734/pantheon.git"
        or required_ref != "refs/heads/dev"
    ):
        raise DispatchError("G2 artifact commit binding policy is invalid")
    authoritative_remote_head = _resolve_authoritative_git_remote_ref(
        required_url,
        required_ref,
    )
    for commit in (artifact_commit, merge_target, authoritative_remote_head):
        _git_output(REPO_ROOT, "cat-file", "-e", f"{commit}^{{commit}}")
    _git_output(
        REPO_ROOT,
        "merge-base",
        "--is-ancestor",
        artifact_commit,
        merge_target,
    )
    _git_output(
        REPO_ROOT,
        "merge-base",
        "--is-ancestor",
        merge_target,
        authoritative_remote_head,
    )
    merge_parents = (
        _git_output(REPO_ROOT, "show", "-s", "--format=%P", merge_target)
        .decode("utf-8")
        .strip()
        .split()
    )
    if len(merge_parents) != 2 or merge_parents[1] != artifact_commit:
        raise DispatchError("G2 artifact merge does not contain its exact head")
    expected_paths = {
        str(contract["evidence_path"]),
        str(contract["canonical_record_bundle_path"]),
        str(contract["hosted_probe_path"]),
        str(contract["closeout_manifest_path"]),
        str(Path(str(contract["closeout_manifest_path"])).with_name("evidence.sha256")),
    }
    if set(artifact_bytes) != expected_paths:
        raise DispatchError("G2 committed artifact set is not exact")
    for relative_path, expected_raw in artifact_bytes.items():
        for commit in (artifact_commit, merge_target):
            committed_raw = _git_output(
                REPO_ROOT,
                "show",
                f"{commit}:{relative_path}",
            )
            if committed_raw != expected_raw:
                raise DispatchError(
                    "G2 admitted artifact is not the committed blob: "
                    f"{relative_path}"
                )
    return {
        "g2_artifact_commit_sha": artifact_commit,
        "g2_artifact_merge_target_sha": merge_target,
        "g2_authoritative_remote_head_sha": authoritative_remote_head,
    }


def _validate_g2_product_evidence(
    *,
    closeout_task: dict[str, Any],
    contract: Mapping[str, Any],
    admission: Mapping[str, Any],
    issued_at: datetime,
    projection_at: datetime,
    captured_at: datetime,
    observed_at: datetime,
    expected_deployment_sha: str,
    closeout_at: datetime,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if set(admission) != G2_CLOSEOUT_ADMISSION_KEYS:
        raise DispatchError("G2 closeout admission schema is not exact")
    if admission.get("review_file") != contract.get("closeout_manifest_path"):
        raise DispatchError("G2 closeout admission review_file mismatch")
    manifest, manifest_raw, _ = _read_g2_artifact(
        contract, "closeout_manifest_path", label="G2 product evidence"
    )
    manifest_sha256 = sha256_bytes(manifest_raw)
    if admission.get("review_manifest_sha256") != manifest_sha256:
        raise DispatchError("G2 product evidence digest mismatch")
    sidecar_relative = str(
        Path(str(contract["closeout_manifest_path"])).with_name(
            "evidence.sha256"
        )
    )
    sidecar_raw = read_rooted_regular_bytes(
        REPO_ROOT,
        sidecar_relative,
        label="G2 evidence sidecar",
    )
    expected_sidecar = f"{manifest_sha256}  evidence.json\n".encode("utf-8")
    if sidecar_raw != expected_sidecar or admission.get(
        "review_manifest_sidecar_sha256"
    ) != sha256_bytes(sidecar_raw):
        raise DispatchError("G2 product evidence sidecar mismatch")
    closeout_projection = _g2_closeout_task_projection(closeout_task)
    if admission.get("task_snapshot_sha256") != canonical_json_sha256(
        closeout_projection
    ):
        raise DispatchError("G2 closeout task snapshot mismatch")

    schema, schema_raw = read_rooted_regular_json(
        REPO_ROOT,
        "schemas/product-evidence.schema.json",
        label="product evidence schema",
    )
    if sha256_bytes(schema_raw) != EXPECTED_PRODUCT_EVIDENCE_SCHEMA_SHA256:
        raise DispatchError("product evidence schema digest mismatch")
    try:
        import jsonschema

        validator_class = jsonschema.validators.validator_for(schema)
        validator_class.check_schema(schema)
        validator = validator_class(schema)
        errors = sorted(
            validator.iter_errors(manifest),
            key=lambda error: tuple(str(part) for part in error.path),
        )
    except Exception as exc:
        raise DispatchError("product evidence schema validation is unavailable") from exc
    if errors:
        raise DispatchError(
            "G2 product evidence schema failed: " + errors[0].message
        )
    task = manifest.get("task")
    owner = str(closeout_task["owner"])
    reviewer = str(closeout_task["reviewer"])
    positive_admissions = {
        "accepted",
        "approved",
        "pass",
        "passed",
        "accepted_product_evidence",
        "review_approved_owner_closeout_ready",
    }
    if (
        not isinstance(task, dict)
        or task.get("id") != contract.get("target_task")
        or task.get("owner") != owner
        or task.get("reviewer") != reviewer
        or task.get("review_file") != contract.get("closeout_manifest_path")
        or task.get("target_environment")
        != contract.get("required_target_environment")
        or task.get("product_level_required") is not True
        or task.get("overall_admission") not in positive_admissions
    ):
        raise DispatchError("G2 product evidence task admission mismatch")
    acceptance = manifest.get("acceptance")
    positive_results = {"pass", "passed", "accepted", "approved"}
    allowed_security_results = positive_results | {"not_applicable"}
    if (
        not isinstance(acceptance, list)
        or not acceptance
        or any(
            not isinstance(row, dict)
            or not isinstance(row.get("id"), str)
            or not str(row["id"]).strip()
            or not isinstance(row.get("evidence_refs"), list)
            or not row["evidence_refs"]
            or any(
                not isinstance(reference, str) or not reference.strip()
                for reference in row["evidence_refs"]
            )
            or len(row["evidence_refs"]) != len(set(row["evidence_refs"]))
            or str(row.get("status") or "").strip().lower().replace("-", "_").replace(" ", "_")
            not in positive_results
            for row in acceptance
        )
    ):
        raise DispatchError("G2 product evidence acceptance is not positive")
    if [row.get("statement") for row in acceptance] != closeout_task.get(
        "acceptance"
    ) or len({str(row.get("id") or "") for row in acceptance}) != len(
        acceptance
    ):
        raise DispatchError(
            "G2 product evidence does not cover the exact target acceptance"
        )
    risks = manifest.get("residual_risks")
    if not isinstance(risks, dict) or any(
        not isinstance(row, dict) or row.get("blocking_for_this_task") is not False
        for row in risks.values()
    ):
        raise DispatchError("G2 product evidence has a blocking residual risk")
    security = manifest.get("security_and_safety")
    if not isinstance(security, dict) or not security:
        raise DispatchError("G2 product evidence security admission is missing")
    retained_security_fields = {
        "environment_boundary",
        "no_live_capital",
        "tenant_isolation",
    }
    deferrable_security_fields = {
        "hosted_frontend",
        "mfa",
        "two_person_approval",
    }
    for field, row in security.items():
        if not isinstance(row, dict):
            raise DispatchError("G2 security evidence row is malformed")
        status_value = str(row.get("status") or "").strip().lower().replace(
            "-", "_"
        ).replace(" ", "_")
        if status_value not in allowed_security_results or (
            field in retained_security_fields and status_value == "not_applicable"
        ):
            raise DispatchError("G2 security evidence is not positive")
        if (
            status_value == "not_applicable"
            and field not in deferrable_security_fields
        ):
            raise DispatchError("G2 security deferral is outside the overlay policy")

    behavioral = manifest.get("behavioral_proof")
    if not isinstance(behavioral, dict) or not behavioral:
        raise DispatchError("G2 behavioral proof is missing")
    for row in behavioral.values():
        if (
            not isinstance(row, dict)
            or str(row.get("status") or "").lower() not in {"pass", "passed"}
            or not isinstance(row.get("proof"), list)
            or not row["proof"]
            or any(
                not isinstance(reference, str) or not reference.strip()
                for reference in row["proof"]
            )
        ):
            raise DispatchError("G2 behavioral proof is not positive")
    hosted_readback = manifest.get("hosted_readback")
    if not isinstance(hosted_readback, dict) or not hosted_readback:
        raise DispatchError("G2 hosted readback is missing")
    for epoch in hosted_readback.values():
        if not isinstance(epoch, dict) or not epoch:
            raise DispatchError("G2 hosted readback epoch is empty")
        positive_observations = 0
        for key, value in epoch.items():
            if key.endswith("_http") and (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 200
                or value >= 400
            ):
                raise DispatchError("G2 hosted readback HTTP result failed")
            if key.endswith("_http"):
                positive_observations += 1
            if key in {"status", "conclusion"}:
                if str(value).lower() not in {"success", "passed", "pass"}:
                    raise DispatchError("G2 hosted readback is not positive")
                positive_observations += 1
        if positive_observations == 0:
            raise DispatchError("G2 hosted readback has no positive observation")

    implementation = manifest.get("implementation_delivery")
    pull_requests: list[dict[str, Any]] = []
    if isinstance(implementation, dict):
        single = implementation.get("pull_request")
        multiple = implementation.get("pull_requests")
        if isinstance(single, dict):
            pull_requests.append(single)
        if isinstance(multiple, list):
            pull_requests.extend(row for row in multiple if isinstance(row, dict))
    matching_pull_requests = [
        row
        for row in pull_requests
        if row.get("base") == "dev"
    ]
    if (
        len(matching_pull_requests) != 1
        or type(matching_pull_requests[0].get("number")) is not int
        or matching_pull_requests[0]["number"] <= 0
        or matching_pull_requests[0].get("url")
        != "https://github.com/ajoe734/pantheon/pull/"
        + str(matching_pull_requests[0].get("number"))
        or not _is_lower_hex(matching_pull_requests[0].get("head_sha"), 40)
        or not _is_lower_hex(matching_pull_requests[0].get("merge_sha"), 40)
        or not isinstance(matching_pull_requests[0].get("merged_at"), str)
    ):
        raise DispatchError("G2 product evidence lacks one exact merged delivery")
    delivery_pull_request = matching_pull_requests[0]
    merged_at = _parse_g2_timestamp(
        delivery_pull_request.get("merged_at"), label="G2 delivery merged_at"
    )
    checks = implementation.get("required_checks") if isinstance(implementation, dict) else None
    if (
        not isinstance(checks, list)
        or not checks
        or any(
            not isinstance(row, dict)
            or str(row.get("conclusion") or "").lower()
            not in {"success", "passed", "pass"}
            for row in checks
        )
    ):
        raise DispatchError("G2 delivery checks are not successful")
    authoritative_pr = _resolve_authoritative_github_pr(
        contract,
        delivery_pull_request["number"],
    )
    authoritative_check_pairs = {
        (row.get("name"), row.get("conclusion"))
        for row in authoritative_pr.get("checks") or []
        if isinstance(row, dict)
    }
    if (
        authoritative_pr.get("repository")
        != contract.get("required_github_repository")
        or authoritative_pr.get("number") != delivery_pull_request["number"]
        or authoritative_pr.get("url") != delivery_pull_request["url"]
        or authoritative_pr.get("state") != "closed"
        or authoritative_pr.get("merged") is not True
        or authoritative_pr.get("merged_at")
        != delivery_pull_request["merged_at"]
        or authoritative_pr.get("base") != delivery_pull_request["base"]
        or authoritative_pr.get("head_sha")
        != delivery_pull_request["head_sha"]
        or authoritative_pr.get("merge_sha")
        != delivery_pull_request["merge_sha"]
        or any(
            (str(row.get("workflow") or ""), str(row.get("conclusion") or ""))
            not in authoritative_check_pairs
            for row in checks
        )
    ):
        raise DispatchError("G2 delivery does not resolve against GitHub truth")
    github_pr_snapshot_sha256 = canonical_json_sha256(authoritative_pr)

    validation = manifest.get("validation")
    commands = validation.get("commands") if isinstance(validation, dict) else None
    validated_at = _parse_g2_timestamp(
        validation.get("validated_at") if isinstance(validation, dict) else None,
        label="G2 product validation timestamp",
    )
    if (
        not isinstance(validation, dict)
        or validation.get("validated_head_sha")
        != delivery_pull_request["head_sha"]
        or not _is_lower_hex(validation.get("validated_base_sha"), 40)
        or not isinstance(commands, list)
        or not commands
        or any(
            not isinstance(row, dict)
            or str(row.get("result") or "").lower()
            not in {"success", "passed", "pass"}
            for row in commands
        )
    ):
        raise DispatchError("G2 product validation is not exact")
    _validate_g2_implementation_git_delivery(
        delivery_pull_request,
        closeout_task,
        str(validation["validated_base_sha"]),
    )

    deployment = manifest.get("deployment")
    if (
        not isinstance(deployment, dict)
        or deployment.get("applicable") is not True
        or deployment.get("environment") != "dev"
    ):
        raise DispatchError("G2 deployment admission is not dev truth")
    for field in ("publish_cut", "canonical_root_deploy"):
        row = deployment.get(field)
        if (
            not isinstance(row, dict)
            or str(row.get("conclusion") or "").lower()
            not in {"success", "passed", "pass"}
            or row.get("deployment_sha") != expected_deployment_sha
        ):
            raise DispatchError("G2 deployment identity is not accepted")
    identity_admission = deployment.get("identity_admission")
    if (
        not isinstance(identity_admission, dict)
        or identity_admission.get("deployment_sha")
        != expected_deployment_sha
        or any(
            str(identity_admission.get(field) or "").lower()
            not in {"success", "passed", "pass", "accepted", "approved"}
            for field in ("status", "conclusion")
            if field in identity_admission
        )
    ):
        raise DispatchError("G2 deployment identity admission mismatch")

    records = manifest.get("record_log")
    decision_kinds = {
        "reviewer_approval_verdict",
        "formal_review_verdict",
        "independent_review_verdict",
        "review_approved",
    }
    approved_statuses = {"approved", "pass", "review_approved"}
    reviewer_decisions = [
        row
        for row in records or []
        if isinstance(row, dict)
        and row.get("actor") == reviewer
        and row.get("kind") in decision_kinds
    ]
    if (
        len(reviewer_decisions) != 1
        or reviewer_decisions[0].get("status") not in approved_statuses
    ):
        raise DispatchError("G2 product evidence reviewer verdict is not exact")
    verdict = reviewer_decisions[0]
    verdict_at = _parse_g2_timestamp(
        verdict.get("recorded_at"), label="G2 reviewer verdict timestamp"
    )
    evidence_cut_at = _parse_g2_timestamp(
        task.get("evidence_cut_at"), label="G2 product evidence cut"
    )
    if (
        projection_at > captured_at
        or captured_at > observed_at
        or observed_at > evidence_cut_at
        or observed_at > validated_at
        or max(evidence_cut_at, validated_at) > verdict_at
        or verdict_at > merged_at
        or merged_at > closeout_at
        or closeout_at > issued_at
    ):
        raise DispatchError("G2 closeout chronology is invalid")
    chronology = [
        projection_at,
        captured_at,
        observed_at,
        evidence_cut_at,
        validated_at,
        verdict_at,
        merged_at,
        closeout_at,
    ]
    if any(
        issued_at - value
        > timedelta(seconds=contract["max_evidence_age_seconds"])
        for value in chronology
    ):
        raise DispatchError("G2 closeout chronology is stale")
    if (
        admission.get("reviewer") != reviewer
        or admission.get("review_verdict_sha256")
        != canonical_json_sha256(verdict)
    ):
        raise DispatchError("G2 reviewer verdict digest mismatch")
    return (
        {
            "product_manifest_sha256": manifest_sha256,
            "product_manifest_sidecar_sha256": sha256_bytes(sidecar_raw),
            "target_task_snapshot_sha256": canonical_json_sha256(
                closeout_projection
            ),
            "reviewer": reviewer,
            "g2_github_pr_snapshot_sha256": github_pr_snapshot_sha256,
            "review_verdict_sha256": canonical_json_sha256(verdict),
            "closeout_at": closeout_at.isoformat().replace("+00:00", "Z"),
        },
        {
            "reviewed_at": verdict["recorded_at"],
            "implementation_pr": {
                "number": delivery_pull_request["number"],
                "head_sha": delivery_pull_request["head_sha"],
                "merge_sha": delivery_pull_request["merge_sha"],
            },
        },
    )


def _validate_g2_reviewer_binding(
    closeout_task: Mapping[str, Any],
    contract: Mapping[str, Any],
    *,
    artifact_commit_sha: str,
    artifact_sha256: Mapping[str, str],
    product_truth: Mapping[str, Any],
    closeout_at: str,
) -> dict[str, str]:
    binding = closeout_task.get("review_binding")
    implementation_pr = (
        binding.get("implementation_pr") if isinstance(binding, dict) else None
    )
    binding_artifacts = (
        binding.get("artifact_sha256") if isinstance(binding, dict) else None
    )
    if (
        not isinstance(binding, dict)
        or set(binding) != G2_REVIEW_BINDING_KEYS
        or binding.get("schema_version") != contract.get("review_binding_schema")
        or binding.get("reviewer") != closeout_task.get("reviewer")
        or binding.get("reviewed_at") != product_truth.get("reviewed_at")
        or not _is_lower_hex(binding.get("artifact_commit_sha"), 40)
        or binding.get("artifact_commit_sha") != artifact_commit_sha
        or not isinstance(binding_artifacts, dict)
        or set(binding_artifacts) != G2_REVIEW_ARTIFACT_DIGEST_FIELDS
        or binding_artifacts != artifact_sha256
        or not isinstance(implementation_pr, dict)
        or set(implementation_pr) != {"number", "head_sha", "merge_sha"}
        or implementation_pr != product_truth.get("implementation_pr")
    ):
        raise DispatchError("G2 reviewer binding is not exact")
    reviewed_at = _parse_g2_timestamp(
        binding.get("reviewed_at"), label="G2 reviewer binding timestamp"
    )
    if reviewed_at > _parse_g2_timestamp(closeout_at, label="G2 closeout timestamp"):
        raise DispatchError("G2 reviewer binding postdates closeout")
    try:
        records = read_activity_audit_records(LOG_PATH)
    except RuntimeError as exc:
        raise DispatchError("G2 reviewer approval audit is unreadable") from exc
    matches = [
        record
        for record in records
        if record.get("type") == "review_approved"
        and record.get("task_id") == closeout_task.get("id")
        and record.get("agent") == closeout_task.get("reviewer")
        and record.get("review_binding") == binding
    ]
    event_fields = {
        "event_id",
        "ts",
        "agent",
        "type",
        "task_id",
        "message",
        "review_binding",
    }
    if len(matches) != 1 or set(matches[0]) != event_fields:
        raise DispatchError("G2 reviewer approval audit is not exact")
    event = matches[0]
    unsigned = {key: deepcopy(value) for key, value in event.items() if key != "event_id"}
    if (
        event.get("ts") != binding.get("reviewed_at")
        or not isinstance(event.get("message"), str)
        or not event["message"].strip()
        or event.get("event_id")
        != "loop-product-event-" + canonical_json_sha256(unsigned)
    ):
        raise DispatchError("G2 reviewer approval audit binding is invalid")
    return {
        "review_binding_sha256": canonical_json_sha256(binding),
        "review_approval_event_sha256": canonical_json_sha256(event),
    }


def _normalized_authoritative_g2_timestamp(value: Any) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise DispatchError("G2 canonical source timestamp is naive")
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    parsed = _parse_g2_timestamp(value, label="G2 canonical source timestamp")
    return parsed.isoformat().replace("+00:00", "Z")


async def _query_authoritative_g2_rows(
    dsn: str,
    identity: Mapping[str, str],
    event_types: list[str],
) -> tuple[dict[str, str], int, list[dict[str, Any]]]:
    try:
        import asyncpg  # type: ignore[import]

        connection = await asyncpg.connect(dsn)
        try:
            async with connection.transaction(
                isolation="repeatable_read",
                readonly=True,
            ):
                isolation = str(
                    await connection.fetchval("SHOW transaction_isolation") or ""
                ).strip().lower()
                read_only = str(
                    await connection.fetchval("SHOW transaction_read_only") or ""
                ).strip().lower()
                source_record = await connection.fetchrow(
                    "SELECT current_database() AS database, "
                    "current_user AS role, current_schema() AS schema, "
                    "to_regclass('public.telemetry_events')::text AS table_name"
                )
                high_watermark = int(
                    await connection.fetchval(
                        "SELECT COALESCE(MAX(ingested_seq), 0) "
                        "FROM public.telemetry_events"
                    )
                    or 0
                )
                records = await connection.fetch(
                    "SELECT ingested_seq, ingested_at, event_id, event_type, "
                    "created_at, payload FROM public.telemetry_events "
                    "WHERE event_type = ANY($1::text[]) "
                    "AND payload #>> '{correlation_envelope,tenant_id}' = $2 "
                    "AND payload #>> '{correlation_envelope,environment}' = $3 "
                    "AND payload #>> '{correlation_envelope,journey_id}' = $4 "
                    "AND payload ->> 'run_id' = $5 "
                    "AND payload ->> 'signal_id' = $6 "
                    "AND payload ->> 'trace_id' = $7 "
                    "ORDER BY ingested_seq",
                    event_types,
                    identity["tenant_id"],
                    identity["environment"],
                    identity["journey_id"],
                    identity["run_id"],
                    identity["signal_id"],
                    identity["trace_id"],
                )
                if isolation != "repeatable read" or read_only not in {
                    "on",
                    "true",
                    "1",
                }:
                    raise DispatchError(
                        "G2 canonical telemetry transaction is not read-only repeatable-read"
                    )
                if not isinstance(source_record, Mapping):
                    raise DispatchError("G2 canonical telemetry identity is missing")
        finally:
            await connection.close()
    except DispatchError:
        raise
    except Exception as exc:
        raise DispatchError(
            "G2 authoritative canonical telemetry query failed"
        ) from exc

    source_identity = {
        "database": str(source_record["database"] or ""),
        "role": str(source_record["role"] or ""),
        "schema": str(source_record["schema"] or ""),
        "table": "telemetry_events"
        if str(source_record["table_name"] or "").split(".")[-1]
        == "telemetry_events"
        else "",
    }
    rows: list[dict[str, Any]] = []
    try:
        for record in records:
            payload = record["payload"]
            if isinstance(payload, str):
                payload = strict_json_loads(
                    payload.encode("utf-8"),
                    source="G2 canonical telemetry payload",
                )
            if not isinstance(payload, Mapping):
                raise DispatchError("G2 canonical telemetry payload is not an object")
            rows.append(
                {
                    "ingested_seq": int(record["ingested_seq"]),
                    "ingested_at": _normalized_authoritative_g2_timestamp(
                        record["ingested_at"]
                    ),
                    "event_id": str(record["event_id"]),
                    "event_type": str(record["event_type"]),
                    "created_at": _normalized_authoritative_g2_timestamp(
                        record["created_at"]
                    ),
                    "payload": dict(payload),
                }
            )
    except DispatchError:
        raise
    except Exception as exc:
        raise DispatchError(
            "G2 authoritative canonical telemetry decode failed"
        ) from exc
    return source_identity, high_watermark, rows


def _read_g2_projection_generation(
    projection_root: Path,
    generation_name: str,
    *,
    label: str,
) -> dict[str, Any]:
    if not re.fullmatch(r"g[0-9]{12}-[0-9a-f]{12}", generation_name):
        raise DispatchError(f"{label} generation name is invalid")
    generations_root = (projection_root / "generations").resolve(strict=True)
    generation = generations_root / generation_name
    try:
        before = generation.lstat()
        resolved = generation.resolve(strict=True)
        resolved.relative_to(generations_root)
    except (OSError, ValueError) as exc:
        raise DispatchError(f"{label} generation is unavailable") from exc
    if generation.is_symlink() or not stat.S_ISDIR(before.st_mode) or resolved != generation:
        raise DispatchError(f"{label} generation directory is not canonical")
    manifest, _ = read_regular_json(
        generation / "manifest.json",
        label=f"{label} manifest",
    )
    journeys, _ = read_regular_json(
        generation / "trade_journey_events.json",
        label=f"{label} journey projection",
    )
    loops, _ = read_regular_json(
        generation / "loop_runs.json",
        label=f"{label} loop projection",
    )
    return {
        "manifest": manifest,
        "trade_journey_events": journeys,
        "loop_runs": loops,
    }


def _resolve_authoritative_g2_snapshot(
    contract: Mapping[str, Any],
    identity: Mapping[str, str],
    generation_name: str,
) -> dict[str, Any]:
    """Re-resolve evidence from live canonical stores, never from its bundle."""

    if (
        contract.get("canonical_source_resolution")
        != "live_read_only_canonical_identity_and_projection_generation_v2"
    ):
        raise DispatchError("G2 canonical source resolution policy mismatch")
    dsn_env = str(contract.get("canonical_telemetry_dsn_env") or "")
    projection_env = str(contract.get("canonical_projection_root_env") or "")
    dsn = str(os.environ.get(dsn_env) or "").strip()
    projection_root_value = str(os.environ.get(projection_env) or "").strip()
    if not dsn or not projection_root_value:
        raise DispatchError("G2 authoritative canonical source configuration is missing")
    try:
        event_order = contract.get("event_order_contract") or {}
        event_types = sorted(
            {
                *list(event_order.get("prefix") or []),
                *list(event_order.get("repeat_group") or []),
                *list(event_order.get("suffix") or []),
            }
        )
        source_identity, source_high_watermark, rows = asyncio.run(
            _query_authoritative_g2_rows(dsn, identity, event_types)
        )
    except RuntimeError as exc:
        raise DispatchError("G2 authoritative canonical query runtime is unavailable") from exc
    try:
        projection_root = Path(projection_root_value).expanduser().resolve(strict=True)
        expected_root = Path(
            str(contract.get("canonical_projection_root") or "")
        )
        if (
            not expected_root.is_absolute()
            or projection_root != expected_root
            or projection_root.stat().st_mode & stat.S_IWOTH
        ):
            raise DispatchError("G2 canonical projection root identity mismatch")
        projection = _read_g2_projection_generation(
            projection_root,
            generation_name,
            label="G2 captured canonical projection",
        )
        current_generation = (projection_root / "current").resolve(strict=True)
        current_generation.relative_to(
            (projection_root / "generations").resolve(strict=True)
        )
        current_projection = _read_g2_projection_generation(
            projection_root,
            current_generation.name,
            label="G2 current canonical projection",
        )
    except Exception as exc:
        raise DispatchError(
            "G2 authoritative canonical projection read failed"
        ) from exc
    expected_source_identity = {
        "database": contract.get("canonical_database_name"),
        "role": contract.get("canonical_database_role"),
        "schema": contract.get("canonical_database_schema"),
        "table": contract.get("canonical_database_table"),
    }
    current_controller = current_projection["loop_runs"].get("controller")
    if (
        source_identity != expected_source_identity
        or not isinstance(current_controller, Mapping)
        or current_controller.get("mode") != "live"
        or current_controller.get("accepted_live") is not True
        or current_controller.get("truth_level") != "canonical_live"
        or current_controller.get("status") != "ready"
        or type(current_controller.get("checkpoint")) is not int
        or current_controller["checkpoint"] < source_high_watermark
        or current_controller.get("backlog") != 0
    ):
        raise DispatchError("G2 canonical source identity or freshness mismatch")
    attestation = {
        **source_identity,
        "projection_root": str(projection_root),
        "live_source_high_watermark": source_high_watermark,
        "captured_generation_name": generation_name,
        "current_generation_name": current_generation.name,
        "current_projection_checkpoint": current_controller["checkpoint"],
        "rows_sha256": canonical_json_sha256(rows),
        "projection_sha256": canonical_json_sha256(projection),
    }
    return {
        "source_high_watermark": source_high_watermark,
        "rows": rows,
        "projection": projection,
        "attestation": attestation,
    }


def _validate_g2_projection_bundle(
    *,
    bundle: Mapping[str, Any],
    contract: Mapping[str, Any],
    identity: Mapping[str, str],
    evidence_records: Mapping[str, Any],
    hosted_probe: Mapping[str, Any],
    issued_at: datetime,
    observed_at: datetime,
) -> tuple[
    list[dict[str, Any]],
    dict[str, Any],
    datetime,
    datetime,
    str,
    dict[str, Any],
]:
    if set(bundle) != G2_RECORD_BUNDLE_KEYS:
        raise DispatchError("G2 canonical record bundle schema is not exact")
    if bundle.get("schema_version") != contract.get("record_bundle_schema"):
        raise DispatchError("G2 canonical record bundle version mismatch")
    source = _require_exact_keys(
        bundle.get("source"), G2_RECORD_BUNDLE_SOURCE_KEYS, label="G2 bundle source"
    )
    if (
        source.get("store") != "telemetry_events"
        or source.get("snapshot_isolation") != "repeatable_read"
        or isinstance(source.get("baseline_high_watermark"), bool)
        or not isinstance(source.get("baseline_high_watermark"), int)
        or isinstance(source.get("source_high_watermark"), bool)
        or not isinstance(source.get("source_high_watermark"), int)
        or source["baseline_high_watermark"] < 0
        or source["source_high_watermark"] <= source["baseline_high_watermark"]
    ):
        raise DispatchError("G2 canonical source snapshot is invalid")
    rows = bundle.get("rows")
    row_keys = {
        "ingested_seq",
        "ingested_at",
        "event_id",
        "event_type",
        "created_at",
        "payload",
    }
    if (
        not isinstance(rows, list)
        or len(rows) < 6
        or any(not isinstance(row, dict) or set(row) != row_keys for row in rows)
    ):
        raise DispatchError("G2 canonical rows are not exact")
    event_ids = [str(row.get("event_id") or "") for row in rows]
    ingested_sequences = [row.get("ingested_seq") for row in rows]
    if (
        any(not event_id for event_id in event_ids)
        or len(event_ids) != len(set(event_ids))
        or any(
            isinstance(value, bool) or not isinstance(value, int)
            for value in ingested_sequences
        )
        or len(ingested_sequences) != len(set(ingested_sequences))
        or ingested_sequences != sorted(ingested_sequences)
        or ingested_sequences[0] <= source["baseline_high_watermark"]
        or ingested_sequences[-1] > source["source_high_watermark"]
    ):
        raise DispatchError("G2 canonical row ordering is invalid")
    created_times = [
        _parse_g2_timestamp(row.get("created_at"), label="G2 row created_at")
        for row in rows
    ]
    ingested_times = [
        _parse_g2_timestamp(row.get("ingested_at"), label="G2 row ingested_at")
        for row in rows
    ]
    if any(
        current >= following
        for current, following in zip(ingested_times, ingested_times[1:])
    ):
        raise DispatchError("G2 canonical ingestion times are not strictly increasing")
    if any(created > ingested for created, ingested in zip(created_times, ingested_times)):
        raise DispatchError("G2 canonical row was ingested before creation")
    for row in rows:
        payload = row.get("payload")
        metadata = payload.get("metadata") if isinstance(payload, dict) else None
        envelope = (
            payload.get("correlation_envelope")
            if isinstance(payload, dict)
            else None
        )
        if (
            not isinstance(payload, dict)
            or not isinstance(metadata, dict)
            or not isinstance(envelope, dict)
            or type(payload.get("sequence_no")) is not int
            or type(metadata.get("sequence_no")) is not int
            or payload.get("sequence_no") != metadata.get("sequence_no")
            or payload.get("sequence_no") <= 0
            or type(envelope.get("producer_revision")) is not int
            or envelope.get("producer_revision") != 1
            or payload.get("execution_mode")
            != contract.get("required_execution_mode")
        ):
            raise DispatchError("G2 canonical sequence number is not an exact integer")
    captured_at = _parse_g2_timestamp(
        bundle.get("captured_at"), label="G2 bundle captured_at"
    )
    if (
        max(ingested_times) > captured_at
        or captured_at > observed_at
        or observed_at > issued_at
    ):
        raise DispatchError("G2 bundle capture time is outside the evidence window")
    try:
        from services.trade_journey import hosted_lifecycle_probe as lifecycle_probe
        from services.trade_journey.lifecycle_projector import _fingerprint
    except Exception as exc:
        raise DispatchError("canonical lifecycle verifier is unavailable") from exc
    all_candidates = lifecycle_probe._complete_candidates(rows)
    resolved_row_ids = {
        event["event_id"]
        for candidate in all_candidates
        for event in candidate.get("selected_events") or []
    }
    if resolved_row_ids != set(event_ids):
        raise DispatchError(
            "G2 bundle contains canonical rows outside complete lifecycles"
        )
    candidates = [
        candidate
        for candidate in all_candidates
        if candidate.get("identity") == dict(identity)
    ]
    if len(candidates) != 1:
        raise DispatchError(
            "G2 bundle does not resolve the declared natural lifecycle"
        )
    candidate = candidates[0]
    selected = candidate.get("selected_events") or []
    if (
        selected[-1]["event_type"] != "reconciliation_completed"
        or candidate.get("identity") != dict(identity)
    ):
        raise DispatchError("G2 natural lifecycle resolution mismatch")
    rows_by_id = {row["event_id"]: row for row in rows}
    selected_created_times = [
        _parse_g2_timestamp(
            rows_by_id[event["event_id"]]["created_at"],
            label="G2 selected row created_at",
        )
        for event in selected
    ]
    if any(
        current >= following
        for current, following in zip(
            selected_created_times, selected_created_times[1:]
        )
    ) or (
        selected_created_times[-1] - selected_created_times[0]
    ).total_seconds() > contract["max_chain_span_seconds"]:
        raise DispatchError("G2 selected lifecycle chronology is invalid")

    projection = _require_exact_keys(
        bundle.get("projection"),
        G2_RECORD_BUNDLE_PROJECTION_KEYS,
        label="G2 projection bundle",
    )
    manifest = _require_exact_keys(
        projection.get("manifest"),
        {"schema_version", "generation", "journey_sha256", "loop_runs_sha256"},
        label="G2 projection manifest",
    )
    journeys = projection.get("trade_journey_events")
    loops = projection.get("loop_runs")
    journey_keys = {
        "schema_version",
        "projector_owned",
        "generation",
        "projection_mode",
        "accepted_live",
        "controller",
        "events",
    }
    loop_keys = journey_keys - {"events"} | {"records"}
    if (
        not isinstance(journeys, dict)
        or set(journeys) != journey_keys
        or not isinstance(loops, dict)
        or set(loops) != loop_keys
        or manifest.get("schema_version")
        != contract.get("projection_manifest_schema")
        or journeys.get("schema_version")
        != contract.get("journey_projection_schema")
        or loops.get("schema_version") != contract.get("loop_run_projection_schema")
        or type(manifest.get("generation")) is not int
        or type(journeys.get("generation")) is not int
        or type(loops.get("generation")) is not int
        or manifest.get("generation") != journeys.get("generation")
        or manifest.get("generation") != loops.get("generation")
        or journeys.get("projector_owned") is not True
        or loops.get("projector_owned") is not True
        or journeys.get("projection_mode") != "live"
        or loops.get("projection_mode") != "live"
        or journeys.get("accepted_live") is not True
        or loops.get("accepted_live") is not True
        or journeys.get("controller") != loops.get("controller")
        or manifest.get("journey_sha256") != _fingerprint(journeys)
        or manifest.get("loop_runs_sha256") != _fingerprint(loops)
    ):
        raise DispatchError("G2 projection bundle integrity mismatch")
    controller = loops.get("controller")
    required_controller = contract["required_projection_controller"]
    if (
        not isinstance(controller, dict)
        or any(controller.get(key) != value for key, value in required_controller.items())
        or controller.get("deployment_sha")
        != hosted_probe.get("expected_deployment_sha")
        or not _is_lower_hex(controller.get("deployment_sha"), 40)
        or controller.get("accepted_live") is not True
        or type(controller.get("checkpoint")) is not int
        or controller["checkpoint"] < source["source_high_watermark"]
        or type(controller.get("backlog")) is not int
        or type(controller.get("generation")) is not int
        or controller.get("generation") != manifest.get("generation")
    ):
        raise DispatchError("G2 projection controller is not canonical live truth")
    projection_times = [
        _parse_g2_timestamp(
            controller.get(field), label=f"G2 projection controller {field}"
        )
        for field in (
            "last_poll_at",
            "last_projection_success_at",
            "last_live_success_at",
        )
    ]
    projection_at = _parse_g2_timestamp(
        controller.get("last_projection_success_at"),
        label="G2 projection controller last_projection_success_at",
    )
    last_live_event_at = _parse_g2_timestamp(
        controller.get("last_live_event_at"),
        label="G2 projection controller last_live_event_at",
    )
    if any(
        value > issued_at
        or issued_at - value
        > timedelta(seconds=contract["max_evidence_age_seconds"])
        for value in projection_times
    ) or projection_at > captured_at or max(ingested_times) > projection_at or not (
        selected_created_times[-1] <= last_live_event_at <= projection_at
    ):
        raise DispatchError("G2 projection controller freshness mismatch")

    projected_events = journeys.get("events")
    if not isinstance(projected_events, list) or any(
        not isinstance(event, dict) for event in projected_events
    ):
        raise DispatchError("G2 journey projection events are malformed")
    projected_ids = [str(event.get("canonical_event_id") or "") for event in projected_events]
    if len(projected_ids) != len(set(projected_ids)) or not set(
        event["event_id"] for event in selected
    ).issubset(set(projected_ids)):
        raise DispatchError("G2 journey projection event set mismatch")
    projected_by_id = {
        str(event["canonical_event_id"]): event for event in projected_events
    }
    selected_rows_by_id = {
        row["event_id"]: row
        for row in rows
        if row["event_id"] in {event["event_id"] for event in selected}
    }
    if set(selected_rows_by_id) != {event["event_id"] for event in selected}:
        raise DispatchError("G2 selected canonical rows are incomplete")
    for row in selected_rows_by_id.values():
        event = projected_by_id[row["event_id"]]
        expected_stage = lifecycle_probe.EXPECTED_STAGES[row["event_type"]]
        if (
            event.get("event_type") != row["event_type"]
            or event.get("stage") != expected_stage
            or event.get("stage_status")
            != contract.get("required_projection_stage_status")
            or event.get("source_mode") != contract.get("required_source_mode")
            or event.get("accepted_live") is not True
            or isinstance(event.get("source_offset"), bool)
            or not isinstance(event.get("source_offset"), int)
            or event.get("source_offset") != row["ingested_seq"]
            or isinstance(event.get("source_sequence_no"), bool)
            or not isinstance(event.get("source_sequence_no"), int)
            or event.get("source_sequence_no")
            != next(
                selected_event["sequence_no"]
                for selected_event in selected
                if selected_event["event_id"] == row["event_id"]
            )
            or any(
                str(event.get(field) or "") != identity[field]
                for field in G2_STABLE_IDENTITY_FIELDS
            )
        ):
            raise DispatchError("G2 journey projection event mismatch")
    loop_records = loops.get("records")
    if (
        not isinstance(loop_records, dict)
        or identity["loop_run_id"] not in loop_records
    ):
        raise DispatchError("G2 loop-run projection set mismatch")
    loop_record = loop_records[identity["loop_run_id"]]
    if (
        not isinstance(loop_record, dict)
        or loop_record.get("id") != identity["loop_run_id"]
        or loop_record.get("status") != contract.get("required_loop_run_status")
        or loop_record.get("source")
        != "canonical_telemetry_lifecycle_projector"
        or loop_record.get("source_modes") != ["live"]
        or loop_record.get("accepted_live") is not True
        or loop_record.get("projection_mode") != "live"
        or loop_record.get("last_canonical_event_id")
        != selected[-1]["event_id"]
        or isinstance(loop_record.get("last_source_offset"), bool)
        or not isinstance(loop_record.get("last_source_offset"), int)
        or loop_record.get("last_source_offset")
        != selected[-1]["ingested_seq"]
        or type(loop_record.get("controller_generation")) is not int
        or loop_record.get("controller_generation") != manifest.get("generation")
        or loop_record.get("deployment_sha") != controller.get("deployment_sha")
        or loop_record.get("last_projected_at")
        != controller.get("last_projection_success_at")
        or any(
            str(loop_record.get(field) or "") != identity[field]
            for field in G2_STABLE_IDENTITY_FIELDS
        )
    ):
        raise DispatchError("G2 loop-run projection record mismatch")

    record_types = contract["record_event_types"]
    rows_by_type = {
        row["event_type"]: row for row in selected_rows_by_id.values()
    }
    for role in ("signal", "order", "fill", "telemetry"):
        reference = _require_exact_keys(
            evidence_records.get(role),
            G2_RECORD_REFERENCE_KEYS,
            label=f"G2 {role} record reference",
        )
        source_row = rows_by_type.get(record_types[role])
        if (
            source_row is None
            or reference.get("event_id") != source_row["event_id"]
            or reference.get("event_type") != source_row["event_type"]
            or reference.get("sha256") != canonical_json_sha256(source_row)
        ):
            raise DispatchError(f"G2 {role} record digest resolution mismatch")
    loop_reference = _require_exact_keys(
        evidence_records.get("loop_run_projection"),
        G2_PROJECTION_REFERENCE_KEYS,
        label="G2 loop projection reference",
    )
    if (
        loop_reference.get("id") != loop_record["id"]
        or loop_reference.get("sha256") != canonical_json_sha256(loop_record)
        or type(loop_reference.get("generation")) is not int
        or loop_reference.get("generation") != manifest["generation"]
        or loop_reference.get("last_canonical_event_id")
        != selected[-1]["event_id"]
    ):
        raise DispatchError("G2 loop projection digest resolution mismatch")

    proof = hosted_probe.get("proof")
    if not isinstance(proof, dict):
        raise DispatchError("G2 hosted proof payload is missing")
    generation_name = ((proof.get("projection") or {}).get("generation_name"))
    if not isinstance(generation_name, str) or not re.fullmatch(
        rf"g{manifest['generation']:012d}-[0-9a-f]{{12}}", generation_name
    ):
        raise DispatchError("G2 hosted projection generation name is invalid")
    try:
        recomputed_proof = lifecycle_probe._correlate(
            candidate=candidate,
            baseline_high_watermark=source["baseline_high_watermark"],
            high_watermark=source["source_high_watermark"],
            journeys=journeys,
            loops=loops,
            generation_name=generation_name,
            expected_sha=controller["deployment_sha"],
        )
    except Exception as exc:
        raise DispatchError("G2 hosted proof cannot be recomputed") from exc
    if proof != recomputed_proof:
        raise DispatchError("G2 hosted proof does not match canonical records")
    authoritative = _resolve_authoritative_g2_snapshot(
        contract,
        identity,
        generation_name,
    )
    attestation = (
        authoritative.get("attestation")
        if isinstance(authoritative, dict)
        else None
    )
    if (
        not isinstance(authoritative, dict)
        or set(authoritative)
        != {"source_high_watermark", "rows", "projection", "attestation"}
        or not isinstance(attestation, dict)
        or set(attestation) != G2_SOURCE_ATTESTATION_KEYS
        or type(authoritative.get("source_high_watermark")) is not int
        or authoritative["source_high_watermark"]
        < source["source_high_watermark"]
        or canonical_json_sha256(authoritative.get("rows"))
        != canonical_json_sha256(rows)
        or canonical_json_sha256(authoritative.get("projection"))
        != canonical_json_sha256(bundle.get("projection"))
        or attestation.get("database") != contract.get("canonical_database_name")
        or attestation.get("role") != contract.get("canonical_database_role")
        or attestation.get("schema") != contract.get("canonical_database_schema")
        or attestation.get("table") != contract.get("canonical_database_table")
        or attestation.get("projection_root")
        != contract.get("canonical_projection_root")
        or attestation.get("live_source_high_watermark")
        != authoritative["source_high_watermark"]
        or attestation.get("captured_generation_name") != generation_name
        or type(attestation.get("current_projection_checkpoint")) is not int
        or attestation["current_projection_checkpoint"]
        < authoritative["source_high_watermark"]
        or attestation.get("rows_sha256")
        != canonical_json_sha256(authoritative.get("rows"))
        or attestation.get("projection_sha256")
        != canonical_json_sha256(authoritative.get("projection"))
    ):
        raise DispatchError(
            "G2 canonical bundle does not resolve against authoritative stores"
        )
    canonical_source_snapshot_sha256 = canonical_json_sha256(authoritative)
    return (
        list(selected_rows_by_id.values()),
        loop_record,
        captured_at,
        projection_at,
        canonical_source_snapshot_sha256,
        deepcopy(attestation),
    )


def _validate_g2_evidence(
    state: dict[str, Any],
    catalog: dict[str, Any],
    *,
    now: datetime,
) -> dict[str, Any]:
    if now.tzinfo is None:
        raise DispatchError("G2 verifier now must be timezone-aware")
    now = now.astimezone(timezone.utc)
    if catalog.get("overlay_applied") is not True:
        raise DispatchError("G2 verifier requires the validated sequencing overlay")
    contract = _require_exact_keys(
        catalog.get("g2_evidence_contract"),
        G2_EVIDENCE_CONTRACT_KEYS,
        label="G2 evidence contract",
    )
    if contract.get("version") != 4:
        raise DispatchError("G2 evidence contract version mismatch")
    _validate_git_repository_trust(REPO_ROOT)
    evidence, evidence_raw, _ = _read_g2_artifact(
        contract, "evidence_path", label="G2 evidence manifest"
    )
    if set(evidence) != G2_EVIDENCE_KEYS:
        raise DispatchError("G2 evidence manifest schema is not exact")
    if (
        evidence.get("schema_version") != "pantheon.loop-prod-g2-evidence.v4"
        or evidence.get("task_id") != contract.get("target_task")
        or evidence.get("program_id") != catalog.get("program_id")
        or evidence.get("target_environment")
        != contract.get("required_target_environment")
    ):
        raise DispatchError("G2 evidence manifest authority mismatch")
    authority = _require_exact_keys(
        evidence.get("authority"), G2_AUTHORITY_KEYS, label="G2 evidence authority"
    )
    source_hashes = catalog.get("source_hashes") or {}
    expected_authority = {
        "tasks_catalog_sha256": source_hashes.get("tasks_catalog_sha256"),
        "sequencing_addendum_sha256": source_hashes.get(
            "sequencing_addendum_sha256"
        ),
        "merge_pr_3737_sha": source_hashes.get("merge_pr_3737_sha"),
        "overlay_sha256": catalog.get("sequencing_overlay_sha256"),
        "target_task_original_contract_sha256": contract.get(
            "target_task_original_contract_sha256"
        ),
        "target_task_amended_contract_sha256": contract.get(
            "target_task_amended_contract_sha256"
        ),
    }
    if authority != expected_authority or any(
        not _is_lower_hex(value, 64 if key != "merge_pr_3737_sha" else 40)
        for key, value in authority.items()
    ):
        raise DispatchError("G2 evidence hash authority mismatch")
    identity = _require_exact_keys(
        evidence.get("identity"), set(G2_STABLE_IDENTITY_FIELDS), label="G2 identity"
    )
    if any(
        not isinstance(identity[field], str) or not identity[field].strip()
        for field in G2_STABLE_IDENTITY_FIELDS
    ):
        raise DispatchError("G2 stable identity is incomplete")
    if identity["environment"] != contract.get("required_record_environment"):
        raise DispatchError("G2 record environment mismatch")

    issued_at = _parse_g2_timestamp(evidence.get("issued_at"), label="G2 issued_at")
    expires_at = _parse_g2_timestamp(evidence.get("expires_at"), label="G2 expires_at")
    max_age = timedelta(seconds=contract["max_evidence_age_seconds"])
    max_skew = timedelta(seconds=contract["max_future_skew_seconds"])
    if (
        issued_at > now + max_skew
        or now > expires_at
        or now - issued_at > max_age
        or expires_at <= issued_at
        or expires_at - issued_at > max_age
    ):
        raise DispatchError("G2 evidence is stale, future-dated, or expired")

    bundle_reference = _require_exact_keys(
        evidence.get("record_bundle"), {"path", "sha256"}, label="G2 bundle reference"
    )
    probe_reference = _require_exact_keys(
        evidence.get("hosted_probe"), {"path", "sha256"}, label="G2 probe reference"
    )
    if (
        bundle_reference.get("path") != contract.get("canonical_record_bundle_path")
        or probe_reference.get("path") != contract.get("hosted_probe_path")
    ):
        raise DispatchError("G2 artifact path binding mismatch")
    bundle, bundle_raw, _ = _read_g2_artifact(
        contract, "canonical_record_bundle_path", label="G2 canonical record bundle"
    )
    hosted, hosted_raw, _ = _read_g2_artifact(
        contract, "hosted_probe_path", label="G2 hosted lifecycle proof"
    )
    if (
        bundle_reference.get("sha256") != sha256_bytes(bundle_raw)
        or probe_reference.get("sha256") != sha256_bytes(hosted_raw)
    ):
        raise DispatchError("G2 artifact raw digest mismatch")
    hosted_keys = {
        "schema_version",
        "task_id",
        "outcome",
        "observed_at",
        "expected_deployment_sha",
        "proof",
        "redaction",
    }
    if (
        set(hosted) != hosted_keys
        or hosted.get("schema_version") != contract.get("hosted_probe_schema")
        or hosted.get("task_id") != "LOOP-PROD-TEL-002"
        or hosted.get("outcome") != "passed"
        or hosted.get("redaction")
        != {"dsn_included": False, "payloads_included": False}
        or not _is_lower_hex(hosted.get("expected_deployment_sha"), 40)
    ):
        raise DispatchError("G2 hosted lifecycle proof admission mismatch")
    observed_at = _parse_g2_timestamp(
        hosted.get("observed_at"), label="G2 hosted proof observed_at"
    )
    if observed_at > issued_at or issued_at - observed_at > max_age:
        raise DispatchError("G2 hosted proof is outside the evidence window")
    records = _require_exact_keys(
        evidence.get("records"), G2_RECORDS_KEYS, label="G2 evidence records"
    )
    (
        rows,
        _,
        captured_at,
        projection_at,
        canonical_source_snapshot_sha256,
        canonical_source_attestation,
    ) = _validate_g2_projection_bundle(
        bundle=bundle,
        contract=contract,
        identity=identity,
        evidence_records=records,
        hosted_probe=hosted,
        issued_at=issued_at,
        observed_at=observed_at,
    )
    earliest = min(
        _parse_g2_timestamp(row["created_at"], label="G2 row created_at")
        for row in rows
    )
    if issued_at - earliest > max_age:
        raise DispatchError("G2 canonical lifecycle is stale")
    closeout_task, closeout_at = _resolve_g2_closeout_task(
        state, catalog, contract
    )
    product_admission, product_truth = _validate_g2_product_evidence(
        closeout_task=closeout_task,
        contract=contract,
        admission=evidence.get("closeout_admission"),
        issued_at=issued_at,
        projection_at=projection_at,
        captured_at=captured_at,
        observed_at=observed_at,
        expected_deployment_sha=str(hosted["expected_deployment_sha"]),
        closeout_at=closeout_at,
    )
    product_raw = read_rooted_regular_bytes(
        REPO_ROOT,
        contract["closeout_manifest_path"],
        label="G2 committed product manifest",
    )
    sidecar_relative = str(
        Path(str(contract["closeout_manifest_path"])).with_name(
            "evidence.sha256"
        )
    )
    sidecar_raw = read_rooted_regular_bytes(
        REPO_ROOT,
        sidecar_relative,
        label="G2 committed product manifest sidecar",
    )
    if (
        product_raw is None
        or sidecar_raw is None
        or sha256_bytes(product_raw)
        != product_admission["product_manifest_sha256"]
        or sha256_bytes(sidecar_raw)
        != product_admission["product_manifest_sidecar_sha256"]
    ):
        raise DispatchError("G2 product artifact changed before Git admission")
    artifact_binding = _validate_g2_committed_artifacts(
        closeout_task,
        contract,
        {
            str(contract["evidence_path"]): evidence_raw,
            str(contract["canonical_record_bundle_path"]): bundle_raw,
            str(contract["hosted_probe_path"]): hosted_raw,
            str(contract["closeout_manifest_path"]): product_raw,
            sidecar_relative: sidecar_raw,
        },
    )
    artifact_sha256 = {
        "g2_evidence_sha256": sha256_bytes(evidence_raw),
        "canonical_record_bundle_sha256": sha256_bytes(bundle_raw),
        "hosted_probe_sha256": sha256_bytes(hosted_raw),
        "product_manifest_sha256": sha256_bytes(product_raw),
        "product_manifest_sidecar_sha256": sha256_bytes(sidecar_raw),
    }
    review_binding = _validate_g2_reviewer_binding(
        closeout_task,
        contract,
        artifact_commit_sha=artifact_binding["g2_artifact_commit_sha"],
        artifact_sha256=artifact_sha256,
        product_truth=product_truth,
        closeout_at=product_admission["closeout_at"],
    )
    return {
        **artifact_sha256,
        "canonical_source_snapshot_sha256": (
            canonical_source_snapshot_sha256
        ),
        "canonical_source_attestation": canonical_source_attestation,
        "g2_issued_at": issued_at.isoformat().replace("+00:00", "Z"),
        **artifact_binding,
        **product_admission,
        **review_binding,
    }


def resolve_g2_evidence_admission(
    state: dict[str, Any],
    catalog: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Return the exact validated byte snapshot used for one release decision."""

    try:
        return _validate_g2_evidence(
            state,
            catalog,
            now=now or datetime.now(timezone.utc),
        )
    except Exception:
        return None


def check_g2_evidence_valid(
    state: dict[str, Any],
    catalog: dict[str, Any] | None = None,
    *,
    now: datetime | None = None,
) -> bool:
    """Return one fail-closed G2 decision over immutable canonical artifacts."""

    try:
        if catalog is None:
            catalog, _ = read_regular_json(
                catalog_path(), label="immutable sequencing catalog"
            )
            overlay_env = str(
                os.environ.get("LOOP_PRODUCT_SEQUENCING_OVERLAY") or ""
            ).strip()
            overlay_path = (
                Path(os.path.expanduser(overlay_env)).resolve()
                if overlay_env
                else DEFAULT_CATALOG_PATH.parent
                / "sequencing-overlay-2026-07-16.json"
            )
            apply_sequencing_overlay(catalog, overlay_path)
        return resolve_g2_evidence_admission(
            state,
            catalog,
            now=now,
        ) is not None
    except Exception:
        return False


def _require_exact_keys(value: Any, expected: set[str], *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise DispatchError(f"{label} schema is not exact")
    return value


def _require_string_list(
    value: Any,
    *,
    label: str,
    allow_empty: bool = True,
) -> list[str]:
    if (
        not isinstance(value, list)
        or (not allow_empty and not value)
        or any(not isinstance(item, str) or not item.strip() for item in value)
        or len(value) != len(set(value))
    ):
        raise DispatchError(f"{label} must be a unique list of non-empty strings")
    return value


def _validate_sequencing_dag(tasks: list[dict[str, Any]]) -> None:
    by_id = {str(task["id"]): task for task in tasks}
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(task_id: str) -> None:
        if task_id in visiting:
            raise DispatchError(f"sequencing overlay dependency cycle at {task_id}")
        if task_id in visited:
            return
        visiting.add(task_id)
        for dependency in by_id[task_id]["depends_on"]:
            if dependency in by_id:
                visit(dependency)
        visiting.remove(task_id)
        visited.add(task_id)

    for task_id in by_id:
        visit(task_id)
    for task_id, task in by_id.items():
        for dependency in task["depends_on"]:
            if dependency in by_id and by_id[dependency]["wave"] > task["wave"]:
                raise DispatchError(
                    f"sequencing overlay wave inversion: {task_id} wave "
                    f"{task['wave']} depends on {dependency} wave "
                    f"{by_id[dependency]['wave']}"
                )


def _validate_g2_overlay_contract(
    contract: Any,
    *,
    overlay_tasks: dict[str, Any],
    original_by_id: dict[str, dict[str, Any]],
    amended_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    value = _require_exact_keys(
        contract, G2_EVIDENCE_CONTRACT_KEYS, label="G2 evidence contract"
    )
    if type(value.get("version")) is not int or value.get("version") != 4:
        raise DispatchError("G2 evidence contract version must be 4")
    target_id = value.get("target_task")
    if (
        not isinstance(target_id, str)
        or target_id not in amended_by_id
        or overlay_tasks[target_id]["classification"]
        != "part of the G2 proof path"
    ):
        raise DispatchError("G2 target task is not an exact G2 task")
    expected_original = task_contract_sha256(original_by_id[target_id])
    expected_amended = task_contract_sha256(amended_by_id[target_id])
    if (
        value.get("target_task_original_contract_sha256") != expected_original
        or value.get("target_task_amended_contract_sha256") != expected_amended
    ):
        raise DispatchError("G2 target task contract digest mismatch")
    for key, expected in EXPECTED_SEQUENCING_SOURCE_HASHES.items():
        if value.get(key) != expected:
            raise DispatchError(f"G2 evidence contract {key} mismatch")
    expected_root = (
        "docs/deployment/evidence/loop-product-level/" + target_id + "/"
    )
    expected_paths = {
        "evidence_path": expected_root + "g2-paper-trade-chain.v4.json",
        "closeout_manifest_path": expected_root + "evidence.json",
        "hosted_probe_path": expected_root + "hosted-lifecycle-proof.v1.json",
        "canonical_record_bundle_path": expected_root
        + "g2-canonical-records.v4.json",
    }
    for key, expected in expected_paths.items():
        if value.get(key) != expected:
            raise DispatchError(f"G2 evidence contract {key} mismatch")
        _safe_repo_relative_path(value[key], label=f"G2 {key}")
    exact_scalars = {
        "bundle_digest_algorithm": "sha256(bytes)",
        "record_digest_algorithm": "sha256(canonical-json)",
        "record_bundle_schema": "pantheon.g2-canonical-record-bundle.v4",
        "hosted_probe_schema": "pantheon.loop-prod-tel-002-hosted-proof.v1",
        "projection_manifest_schema": "pantheon.lifecycle-projection-bundle.v1",
        "journey_projection_schema": "pantheon.trade-journey-projection.v1",
        "loop_run_projection_schema": "pantheon.loop-run-projection.v1",
        "required_target_environment": "dev",
        "required_record_environment": "paper",
        "required_execution_mode": "paper",
        "required_source_mode": "live",
        "required_projection_stage_status": "succeeded",
        "required_loop_run_status": "completed",
        "canonical_source_resolution": (
            "live_read_only_canonical_identity_and_projection_generation_v2"
        ),
        "canonical_telemetry_dsn_env": "TELEMETRY_DB_DSN",
        "canonical_database_name": "pantheon",
        "canonical_database_role": "pantheon_app",
        "canonical_database_schema": "public",
        "canonical_database_table": "telemetry_events",
        "canonical_projection_root_env": "LIFECYCLE_PROJECTION_ROOT",
        "canonical_projection_root": "/data/bff/lifecycle-projection",
        "artifact_commit_binding": "reviewer_and_github_bound_git_tree_v2",
        "required_git_remote_url": "https://github.com/ajoe734/pantheon.git",
        "required_git_remote_ref": "refs/heads/dev",
        "required_github_api_base_url": "https://api.github.com",
        "required_github_repository": "ajoe734/pantheon",
        "review_binding_schema": "pantheon.g2-review-binding.v1",
    }
    if any(value.get(key) != expected for key, expected in exact_scalars.items()):
        raise DispatchError("G2 evidence contract scalar policy mismatch")
    exact_limits = {
        "max_evidence_age_seconds": 86400,
        "max_chain_span_seconds": 3600,
        "max_future_skew_seconds": 300,
    }
    if any(
        isinstance(value.get(key), bool) or value.get(key) != expected
        for key, expected in exact_limits.items()
    ):
        raise DispatchError("G2 evidence freshness policy mismatch")
    if value.get("stable_identity_fields") != list(G2_STABLE_IDENTITY_FIELDS):
        raise DispatchError("G2 stable identity contract mismatch")
    event_order = value.get("event_order_contract")
    if (
        not isinstance(event_order, dict)
        or type(event_order.get("minimum_repeat_count")) is not int
        or event_order
        != {
        "prefix": ["signal_generation", "trade_decision"],
        "repeat_group": [
            "order_submitted",
            "paper_fill_simulated",
            "position_snapshot",
        ],
        "minimum_repeat_count": 1,
        "suffix": ["reconciliation_completed"],
        }
    ):
        raise DispatchError("G2 event order contract mismatch")
    if value.get("record_event_types") != {
        "signal": "signal_generation",
        "order": "order_submitted",
        "fill": "paper_fill_simulated",
        "telemetry": "reconciliation_completed",
    }:
        raise DispatchError("G2 record role contract mismatch")
    if value.get("required_projection_controller") != {
        "mode": "live",
        "accepted_live": True,
        "truth_level": "canonical_live",
        "status": "ready",
        "backlog": 0,
    }:
        raise DispatchError("G2 projection controller contract mismatch")
    return value


def apply_sequencing_overlay(catalog: dict[str, Any], overlay_path: Path) -> None:
    """Validate the complete overlay against immutable inputs, then apply atomically."""

    overlay, overlay_raw = read_regular_json(
        overlay_path, label="sequencing overlay"
    )
    overlay_sha256 = sha256_bytes(overlay_raw)
    if overlay_sha256 != EXPECTED_SEQUENCING_OVERLAY_SHA256:
        raise DispatchError("sequencing overlay digest mismatch")
    _require_exact_keys(overlay, SEQUENCING_OVERLAY_KEYS, label="sequencing overlay")
    if overlay.get("schema_version") != 2:
        raise DispatchError("sequencing overlay schema_version must be 2")

    source_hashes = _require_exact_keys(
        overlay.get("source_hashes"),
        set(EXPECTED_SEQUENCING_SOURCE_HASHES),
        label="sequencing overlay source_hashes",
    )
    if source_hashes != EXPECTED_SEQUENCING_SOURCE_HASHES:
        raise DispatchError("sequencing overlay source hash authority mismatch")
    source_catalog, source_catalog_raw = read_regular_json(
        catalog_path(), label="immutable sequencing catalog"
    )
    if sha256_bytes(source_catalog_raw) != source_hashes["tasks_catalog_sha256"]:
        raise DispatchError("immutable sequencing catalog digest mismatch")
    if canonical_json_sha256(source_catalog) != canonical_json_sha256(catalog):
        raise DispatchError("catalog object diverges from immutable sequencing source")
    addendum_raw = read_regular_bytes(
        SEQUENCING_ADDENDUM_PATH, label="immutable sequencing addendum"
    )
    if sha256_bytes(addendum_raw) != source_hashes["sequencing_addendum_sha256"]:
        raise DispatchError("immutable sequencing addendum digest mismatch")

    original_tasks = catalog.get("tasks")
    if (
        not isinstance(original_tasks, list)
        or len(original_tasks) != 48
        or any(not isinstance(task, dict) for task in original_tasks)
    ):
        raise DispatchError("immutable sequencing catalog must contain 48 task objects")
    original_ids = [str(task.get("id") or "") for task in original_tasks]
    if any(not task_id for task_id in original_ids) or len(original_ids) != len(
        set(original_ids)
    ):
        raise DispatchError("immutable sequencing catalog task IDs are invalid")
    original_by_id = {
        str(task["id"]): deepcopy(task) for task in original_tasks
    }
    overlay_tasks = overlay.get("tasks")
    if not isinstance(overlay_tasks, dict):
        raise DispatchError("sequencing overlay tasks must be an object")
    overlay_ids = set(overlay_tasks)
    catalog_ids = set(original_by_id)
    if overlay_ids != catalog_ids:
        raise DispatchError(
            "sequencing overlay exact task set mismatch: "
            f"missing={sorted(catalog_ids - overlay_ids)} "
            f"extra={sorted(overlay_ids - catalog_ids)}"
        )

    candidate = deepcopy(catalog)
    candidate_by_id = {str(task["id"]): task for task in candidate["tasks"]}
    for task_id in original_ids:
        entry = _require_exact_keys(
            overlay_tasks[task_id],
            SEQUENCING_TASK_ENTRY_KEYS,
            label=f"sequencing entry {task_id}",
        )
        wave = entry.get("wave")
        if isinstance(wave, bool) or not isinstance(wave, int) or wave < 0:
            raise DispatchError(f"sequencing entry {task_id} wave is invalid")
        classification = entry.get("classification")
        rationale = entry.get("rationale")
        if classification not in SEQUENCING_CLASSIFICATIONS:
            raise DispatchError(
                f"sequencing entry {task_id} classification is invalid"
            )
        if not isinstance(rationale, str) or not rationale.strip():
            raise DispatchError(f"sequencing entry {task_id} rationale is empty")
        original_dependencies = _require_string_list(
            entry.get("original_depends_on"),
            label=f"sequencing entry {task_id} original dependencies",
        )
        amended_dependencies = _require_string_list(
            entry.get("amended_depends_on"),
            label=f"sequencing entry {task_id} amended dependencies",
        )
        if original_dependencies != original_by_id[task_id].get("depends_on"):
            raise DispatchError(
                f"sequencing entry {task_id} original dependency mismatch"
            )
        if task_id in amended_dependencies:
            raise DispatchError(f"sequencing entry {task_id} depends on itself")
        unknown_external = {
            dependency
            for dependency in amended_dependencies
            if dependency not in catalog_ids
            and dependency not in set(original_dependencies)
        }
        if unknown_external:
            raise DispatchError(
                f"sequencing entry {task_id} adds unknown external dependencies: "
                + ", ".join(sorted(unknown_external))
            )
        target = candidate_by_id[task_id]
        target["wave"] = wave
        target["depends_on"] = deepcopy(amended_dependencies)

    _validate_sequencing_dag(candidate["tasks"])
    amended_by_id = {str(task["id"]): task for task in candidate["tasks"]}
    release_gate = _require_exact_keys(
        overlay.get("release_gate"), RELEASE_GATE_KEYS, label="release gate"
    )
    if (
        type(release_gate.get("version")) is not int
        or release_gate.get("version") != 1
        or release_gate.get("gate_id") != "hardening-after-g2-paper-trade-v1"
        or release_gate.get("gated_classifications")
        != sorted(GATED_SEQUENCING_CLASSIFICATIONS)
        or release_gate.get("release_predicate")
        != "g2_evidence_contract_v4_valid"
        or release_gate.get("pre_gate_action")
        != "park_new_and_existing_gated_tasks_allow_ungated"
        or release_gate.get("post_gate_action")
        != "allow_dependency_governed_materialization"
    ):
        raise DispatchError("release gate policy mismatch")
    gated_ids = _require_string_list(
        release_gate.get("gated_task_ids"),
        label="release gate task IDs",
        allow_empty=False,
    )
    expected_gated_ids = {
        task_id
        for task_id, entry in overlay_tasks.items()
        if entry["classification"] in GATED_SEQUENCING_CLASSIFICATIONS
    }
    if set(gated_ids) != expected_gated_ids:
        raise DispatchError("release gate task set does not match classifications")

    deferral = _require_exact_keys(
        overlay.get("acceptance_deferral"),
        ACCEPTANCE_DEFERRAL_KEYS,
        label="acceptance deferral",
    )
    if (
        type(deferral.get("version")) is not int
        or deferral.get("version") != 1
        or deferral.get("policy_id")
        != "pre-g2-strict-only-acceptance-deferral-v1"
        or deferral.get("release_gate_id") != release_gate["gate_id"]
        or deferral.get("catalog_acceptance_immutable") is not True
        or deferral.get("applies_to_classifications")
        != [
            "permitted before the paper-trade proof",
            "part of the G2 proof path",
        ]
        or deferral.get("deferred_dimensions")
        != [
            "strict_auth",
            "browser_dev_bearer_removal",
            "mfa",
            "two_person",
            "negative_identity",
        ]
        or deferral.get("retained_dimensions")
        != [
            "tenant_isolation",
            "environment_binding",
            "paper_execution",
            "no_live_capital",
        ]
        or deferral.get("materialized_acceptance_action")
        != "preserve_catalog_acceptance_unchanged"
    ):
        raise DispatchError("acceptance deferral policy mismatch")
    pre_g2_ids = _require_string_list(
        deferral.get("applies_to_task_ids"),
        label="acceptance deferral task IDs",
        allow_empty=False,
    )
    expected_pre_g2_ids = {
        task_id
        for task_id, entry in overlay_tasks.items()
        if entry["classification"] in PRE_G2_SEQUENCING_CLASSIFICATIONS
    }
    if set(pre_g2_ids) != expected_pre_g2_ids:
        raise DispatchError(
            "acceptance deferral task set does not match classifications"
        )
    for task_id in original_ids:
        if candidate_by_id[task_id].get("acceptance") != original_by_id[
            task_id
        ].get("acceptance"):
            raise DispatchError("sequencing overlay changed catalog acceptance")

    g2_contract = _validate_g2_overlay_contract(
        overlay.get("g2_evidence_contract"),
        overlay_tasks=overlay_tasks,
        original_by_id=original_by_id,
        amended_by_id=amended_by_id,
    )
    if expected_gated_ids & expected_pre_g2_ids or (
        expected_gated_ids | expected_pre_g2_ids
    ) != catalog_ids:
        raise DispatchError("sequencing release partitions are not exact")

    def internal_ancestors(task_id: str) -> set[str]:
        result: set[str] = set()
        pending = list(amended_by_id[task_id]["depends_on"])
        while pending:
            dependency = pending.pop()
            if dependency not in amended_by_id or dependency in result:
                continue
            result.add(dependency)
            pending.extend(amended_by_id[dependency]["depends_on"])
        return result

    for task_id in expected_pre_g2_ids:
        if internal_ancestors(task_id) & expected_gated_ids:
            raise DispatchError(
                f"pre-G2 task {task_id} has a gated dependency ancestor"
            )
    target_id = str(g2_contract["target_task"])
    for task_id in expected_gated_ids:
        if target_id not in internal_ancestors(task_id):
            raise DispatchError(
                f"gated task {task_id} is not downstream of the G2 checkpoint"
            )
    candidate["source_hashes"] = deepcopy(source_hashes)
    candidate["sequencing_entries"] = deepcopy(overlay_tasks)
    candidate["original_task_contract_sha256s"] = {
        task_id: task_contract_sha256(task)
        for task_id, task in original_by_id.items()
    }
    candidate["release_gate"] = deepcopy(release_gate)
    candidate["acceptance_deferral"] = deepcopy(deferral)
    candidate["g2_evidence_contract"] = deepcopy(g2_contract)
    candidate["sequencing_overlay_sha256"] = overlay_sha256
    candidate["overlay_applied"] = True
    catalog.clear()
    catalog.update(candidate)


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
    catalog, catalog_bytes = read_regular_json(
        selected_catalog_path, label="loop product task catalog"
    )
    tasks = validate_catalog(catalog, selected_catalog_path)
    source_catalog = deepcopy(catalog)
    source_catalog_digest = sha256_bytes(catalog_bytes)

    # The authoritative overlay is the production default. A base-catalog
    # opt-out exists only for isolated pytest status roots that exercise the
    # historical dispatcher contract; it cannot disable sequencing live.
    overlay_path = None
    if getattr(args, "sequencing_overlay", ""):
        overlay_path = Path(os.path.expanduser(args.sequencing_overlay)).resolve()
    else:
        overlay_env = os.environ.get("LOOP_PRODUCT_SEQUENCING_OVERLAY")
        if overlay_env:
            overlay_path = Path(os.path.expanduser(overlay_env)).resolve()
        else:
            system_temp_root = Path(tempfile.gettempdir()).resolve()
            test_base_catalog = (
                os.environ.get("LOOP_PRODUCT_TEST_BASE_CATALOG") == "1"
                and bool(os.environ.get("PYTEST_CURRENT_TEST"))
                and STATUS_ROOT != REPO_ROOT
                and STATUS_ROOT.parent == system_temp_root
                and STATUS_ROOT.name.startswith("tmp")
            )
            if not test_base_catalog:
                overlay_path = DEFAULT_SEQUENCING_OVERLAY_PATH.resolve()

    if overlay_path:
        print(f"Applying sequencing overlay from: {overlay_path}")
        apply_sequencing_overlay(catalog, overlay_path)
        tasks = catalog["tasks"]
        catalog_digest = canonical_json_sha256(catalog)
    else:
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
        if args.dry_run:
            assert_activity_audit_stable_unlocked(LOG_PATH)
        else:
            prepare_activity_audit_unlocked(LOG_PATH)
        original_signature = file_signature(STATUS_PATH)
        state = read_json(STATUS_PATH)
        pending = state.get("program_activity_outbox")
        if pending is not None:
            validated_pending = validate_pending_sequencing_recovery(
                state,
                catalog,
                catalog_digest,
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
        graph_prestate = validate_program_graph_prestate(
            state,
            catalog,
            catalog_digest,
            source_catalog=(source_catalog if overlay_path else None),
            source_catalog_digest=(source_catalog_digest if overlay_path else None),
        )
        proposed = deepcopy(state)

        timestamp = iso_now()
        migration_catalog = source_catalog if overlay_path else catalog
        migration_catalog_digest = (
            source_catalog_digest if overlay_path else catalog_digest
        )
        if overlay_path and graph_prestate == "current":
            validate_migration_records(
                proposed.get("program_catalog_migrations") or [],
                migration_catalog,
                migration_catalog_digest,
            )
            migrated, migration_logs, migration_changed = [], [], False
        else:
            migrated, migration_logs, migration_changed = apply_catalog_migrations(
                proposed,
                migration_catalog,
                migration_catalog_digest,
                timestamp,
            )
        sequencing_migrated, sequencing_logs, sequencing_changed = (
            install_sequencing_epoch(
                proposed,
                source_catalog,
                source_catalog_digest,
                catalog,
                catalog_digest,
                timestamp,
                graph_prestate=graph_prestate,
            )
            if overlay_path
            else ([], [], False)
        )
        migrated = [*migrated, *sequencing_migrated]
        created, preserved, archived, logs, changed = materialize(
            proposed,
            tasks,
            catalog,
            catalog_digest,
            timestamp,
        )
        fresh_sequencing_logs, fresh_sequencing_changed = (
            install_fresh_sequencing_epoch(
                proposed,
                source_catalog,
                source_catalog_digest,
                catalog,
                catalog_digest,
                timestamp,
                graph_prestate=graph_prestate,
            )
            if overlay_path
            else ([], False)
        )
        overlay_logs, overlay_changed = ensure_completion_overlay(
            proposed,
            catalog,
            catalog_digest,
            timestamp,
            graph_prestate=graph_prestate,
        )
        logs = [
            *migration_logs,
            *sequencing_logs,
            *logs,
            *fresh_sequencing_logs,
            *overlay_logs,
        ]
        changed = (
            migration_changed
            or sequencing_changed
            or fresh_sequencing_changed
            or overlay_changed
            or changed
        )
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
        if overlay_path:
            if validate_sequencing_epoch_record(
                proposed, catalog, catalog_digest
            ) is None:
                raise DispatchError(
                    "sequencing transaction did not produce an immutable epoch"
                )
            releases = proposed.get(PROGRAM_SEQUENCING_RELEASES_STATE_KEY) or {}
            if not isinstance(releases, dict):
                raise DispatchError("program sequencing releases must be an object")
            if str(catalog["program_id"]) in releases and (
                validate_sequencing_release_record(
                    proposed, catalog, catalog_digest
                )
                is None
            ):
                raise DispatchError(
                    "sequencing transaction did not produce an immutable release"
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
