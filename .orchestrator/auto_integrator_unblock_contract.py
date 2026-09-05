"""Pure shared contract for auto-integrator unblock requests."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping


REQUEST_SCHEMA = "pantheon-auto-integrator-unblock-request/v1"
REQUEST_INBOX = ".orchestrator/auto-integrator-unblock-inbox"
RECEIPT_SCHEMA = "pantheon-auto-integrator-unblock-receipt/v1"
RECEIPT_ROOT = ".orchestrator/auto-integrator-unblock-receipts"
TASK_ID_LIMIT = 96
REQUEST_FIELDS = frozenset(
    {
        "schema", "status_root", "status_identity_sha256", "command_runtime_sha",
        "source_task_id", "source_task_generation", "unblock_task_id", "reason",
        "detail", "repository_id", "repository_slug", "pr", "head_sha", "owner",
        "reviewer",
    }
)
REASONS = frozenset(
    {
        "ambiguous-open-prs", "auto-merge-revocation-failed", "base-branch-mismatch",
        "canonical-authority-lock-failed", "canonical-state-refresh-failed", "ci-red",
        "dirty-repository-checkout", "exact-head-merge-conflict", "exact-head-missing",
        "final-auto-merge-armed", "final-base-branch-mismatch", "final-ci-not-green",
        "final-ci-red", "final-head-branch-mismatch", "final-head-changed",
        "final-pr-changed", "final-pr-is-draft", "final-pr-missing",
        "final-pr-refresh-failed", "final-repository-mismatch",
        "final-review-contract-changed", "final-review-gate-changed",
        "final-merge-state-not-direct", "final-authority-timeout", "head-branch-mismatch",
        "integration-checkout-identity-mismatch", "integration-checkout-not-detached",
        "integration-checkout-not-standalone", "git-common-dir-not-writable",
        "invalid-git-common-dir", "invalid-git-repository", "invalid-repository-root",
        "invalid-repository-scope", "merge-state-blocked", "merge-state-dirty",
        "merge-state-draft", "missing-dedicated-integration-path", "missing-origin-remote",
        "missing-pr", "merged-pr-no-merge-commit", "missing-repository-checkout",
        "missing-repository-slug", "pr-is-draft", "pr-lookup-failed", "rebase-conflict",
        "repository-checkout-not-writable", "repository-mismatch",
        "repository-origin-mismatch", "repository-status-unavailable", "smoke-failed",
        "task-brief-carry-forward-publication-failed",
    }
    | {
        "review-gate-" + reason.replace("_", "-")
        for reason in {
            "approval_audit_unreadable", "approval_base_mismatch", "approval_binding_unusable",
            "approval_head_binding_missing", "approval_head_branch_mismatch",
            "approval_head_mismatch", "approval_pr_mismatch", "approval_record_missing",
            "approval_reviewer_mismatch", "approval_revoked",
            "approval_timestamp_not_credible", "auto_merge_request_outlived_head",
            "base_branch_mismatch", "declared_head_branch_mismatch",
            "declared_head_sha_mismatch", "head_branch_mismatch",
            "head_changed_after_approval", "merged_before_approval", "merge_timestamp_unknown",
            "no_independent_reviewer", "pr_head_timestamp_unknown", "pr_head_unknown",
            "pr_is_draft", "pr_missing", "review_not_approved", "task_state_unavailable",
        }
    }
)


def validate_reason(reason: Any) -> str:
    """Return a known finite producer reason, rejecting all other values."""

    if not isinstance(reason, str) or reason not in REASONS:
        raise ValueError("unblock request reason is not allowed")
    return reason


def task_id(
    source_task_id: str,
    reason: str,
    *,
    source_task_generation: int,
    repository_slug: str,
    pr: int,
    head_sha: str,
) -> str:
    scalar_strings = {
        "source_task_id": source_task_id,
        "reason": reason,
        "repository_slug": repository_slug,
        "head_sha": head_sha,
    }
    for name, value in scalar_strings.items():
        if not isinstance(value, str) or not value:
            raise ValueError(f"unblock identity {name} must be a non-empty string")
    for name, value in {
        "source_task_generation": source_task_generation,
        "pr": pr,
    }.items():
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"unblock identity {name} must be a positive integer")
    safe_reason = "".join(
        character if character.isalnum() else "-" for character in reason.upper()
    ).strip("-")
    readable = f"INTEGRATION-UNBLOCK-{source_task_id}-{safe_reason}"
    candidate = {
        "source_task_id": source_task_id,
        "source_task_generation": source_task_generation,
        "repository_slug": repository_slug,
        "pr": pr,
        "head_sha": head_sha.lower(),
        "reason": reason,
    }
    suffix = hashlib.sha256(canonical_bytes(candidate)).hexdigest()[:12].upper()
    return f"{readable[: TASK_ID_LIMIT - len(suffix) - 1].rstrip('-')}-{suffix}"


def task_id_from_identity(identity: Mapping[str, Any]) -> str:
    """Derive an ID from raw request identity without scalar coercion."""

    return task_id(
        identity.get("source_task_id"),
        identity.get("reason"),
        source_task_generation=identity.get("source_task_generation"),
        repository_slug=identity.get("repository_slug"),
        pr=identity.get("pr"),
        head_sha=identity.get("head_sha"),
    )


def canonical_bytes(request: Mapping[str, Any]) -> bytes:
    return json.dumps(
        request, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def request_filename(request: Mapping[str, Any]) -> str:
    return f"{hashlib.sha256(canonical_bytes(request)).hexdigest()}.json"
