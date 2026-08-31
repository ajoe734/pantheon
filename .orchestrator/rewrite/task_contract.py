"""Canonical task-contract rules shared by materialization and scheduling."""
from __future__ import annotations

import re
from typing import Any, Iterable, Mapping


def acceptance_identity_mentions(
    acceptance: Any,
    identities: Iterable[str],
) -> dict[str, list[str]]:
    """Return configured agent identities explicitly named by acceptance text."""

    raw_lines = [acceptance] if isinstance(acceptance, str) else (acceptance or [])
    lines = [str(value) for value in raw_lines if str(value).strip()]
    mentions: dict[str, list[str]] = {}
    for raw_identity in identities:
        identity = str(raw_identity or "").strip()
        if not identity:
            continue
        pattern = re.compile(
            rf"(?<![\w]){re.escape(identity)}(?![\w])",
            re.IGNORECASE,
        )
        matched = [line for line in lines if pattern.search(line)]
        if matched:
            mentions[identity] = matched
    return mentions


def validate_role_based_acceptance(
    acceptance: Any,
    identities: Iterable[str],
) -> None:
    """Require new task acceptance to describe roles, not fleet slots."""

    mentions = acceptance_identity_mentions(acceptance, identities)
    if mentions:
        raise ValueError(
            "task acceptance must use owner/reviewer role names instead of "
            "configured agent identities: " + ", ".join(sorted(mentions))
        )


def validate_reassignment_against_acceptance(
    task: Mapping[str, Any],
    *,
    new_owner: str,
    new_reviewer: str,
) -> None:
    """Refuse silently invalidating a legacy identity-pinned contract."""

    changed_identities: list[str] = []
    old_owner = str(task.get("owner") or "").strip()
    old_reviewer = str(task.get("reviewer") or "").strip()
    if old_owner and old_owner.casefold() != str(new_owner or "").strip().casefold():
        changed_identities.append(old_owner)
    if old_reviewer and old_reviewer.casefold() != str(new_reviewer or "").strip().casefold():
        changed_identities.append(old_reviewer)
    mentions = acceptance_identity_mentions(task.get("acceptance"), changed_identities)
    if mentions:
        raise ValueError(
            "reassignment would contradict identity-pinned acceptance; supersede "
            "the task with role-based acceptance first: "
            + ", ".join(sorted(mentions))
        )


# ---------------------------------------------------------------------------
# DTG-CLEAN-M3: delivery binding, exact-head, manifest, and review/merged-
# evidence validation.
#
# Per SD.md 7.6: pure delivery binding, exact-head, manifest, review, and
# merged-evidence validation live here; GitHub calls stay in the existing
# bridge/integrator modules (scripts/git/github_review_bridge.py,
# scripts/git/task_review_merge_gate.py) and canonical mutation stays in
# TaskStore/status ownership (scripts/ai_status.py). Every function below is
# a pure Mapping-in/verdict-or-binding-out validator or resolver -- none of
# them call save_state/sync_all or otherwise mutate canonical state.
#
# Module-boundary note: two symbols this closure touches
# (``_canonical_json_sha256``, ``_github_review_bridge_module``) are used far
# beyond delivery-evidence validation, so they stay owned by ai_status.py and
# are reached through a lazy (function-body) import -- the same established
# pattern already used for DTG-CLEAN-M1 (rewrite/status_projection.py) and
# DTG-CLEAN-M2 (development_bridge/dev_bridge_materialize.py).
# ``github_review_bridge_evidence_matches`` moved to rewrite/status_projection.py
# in DTG-CLEAN-M1 (it was, at the time, only called by a dashboard-only
# function); it is imported directly from there now that
# exact_head_acceptance_evidence_matches also needs it, since that is a
# normal one-directional import with no cycle.
# ---------------------------------------------------------------------------

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from pathlib import PurePosixPath

from multi_repo_registry import (
    artifact_repository_id,
    repository_relative_artifact_path,
    repository_slug,
    validate_task_repository_scope,
)
from rewrite import task_machine
from rewrite.status_projection import github_review_bridge_evidence_matches


def _ai_status_module():
    """Lazy import back to the entrypoint module for the handful of symbols
    that are genuinely shared infrastructure, not delivery-evidence-specific
    (see module section docstring above)."""
    scripts_dir = Path(__file__).resolve().parents[2] / "scripts"
    import sys

    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    import ai_status

    return ai_status


def _delivery_contract_payload(task: Mapping[str, Any]) -> dict[str, Any]:
    """Return the immutable work contract a non-PR reviewer must inspect."""

    return dict(task_machine.delivery_contract_payload(task))


def _extract_pr_number(value: Any) -> int | None:
    if isinstance(value, int) and value > 0:
        return value
    text = str(value or "").strip().lstrip("#")
    if text.isdigit() and int(text) > 0:
        return int(text)
    match = re.search(r"/pull/(\d+)", text, re.IGNORECASE)
    if match:
        return int(match.group(1))
    return None


def _validated_pr_binding(binding: Mapping[str, Any], task_id: str) -> dict[str, Any]:
    ai_status = _ai_status_module()
    raw_pr = _extract_pr_number(binding.get("pr"))
    head_sha = str(binding.get("head_sha") or "").strip().lower()
    if raw_pr is None or not ai_status.APPROVAL_HEAD_SHA_RE.fullmatch(head_sha):
        raise SystemExit(f"{task_id} has an invalid pull-request delivery binding")
    return {
        "pr": raw_pr,
        "head_sha": head_sha,
        "head_branch": str(binding.get("head_branch") or "").strip()
        or f"task/{task_id}",
        "base": str(binding.get("base") or ai_status.DEFAULT_APPROVAL_BASE_BRANCH).strip()
        or ai_status.DEFAULT_APPROVAL_BASE_BRANCH,
    }


def validate_handoff_pr_delivery_binding(
    task: Mapping[str, Any],
    config: dict[str, Any],
    binding: Mapping[str, Any],
    *,
    review_file: str | None = None,
) -> dict[str, Any]:
    """Return the one complete review-admission binding, or fail closed."""
    ai_status = _ai_status_module()

    task_id = str(task.get("id") or "").strip()
    normalized = _validated_pr_binding(binding, task_id)
    try:
        repository_id = validate_task_repository_scope(config, dict(task))
    except (ValueError, RuntimeError) as exc:
        raise SystemExit(
            f"PR handoff requires one delivery repository with a configured "
            f"GitHub slug for {task_id or '?'}: {exc}"
        ) from exc
    repository_slug_value = repository_slug(config, repository_id)
    if not repository_slug_value:
        raise SystemExit(
            "PR handoff requires one delivery repository with a configured "
            f"GitHub slug for {task_id or '?'}"
        )
    github_review_bridge = ai_status._github_review_bridge_module()
    manifest_path = (
        str(review_file).strip()
        if review_file is not None
        else os.environ.get("REVIEW_FILE", "").strip()
    )
    if not manifest_path:
        raise SystemExit(
            f"PR handoff requires REVIEW_FILE for {task_id or '?'}; the evidence "
            "manifest must be committed in the exact PR head before review"
        )
    manifest_path = validate_review_manifest_contract_path(
        task,
        config,
        repository_id=repository_id,
        review_file=manifest_path,
    )
    try:
        validated = github_review_bridge.validate_review_admission(
            repository=repository_slug_value,
            binding=normalized,
            review_file=manifest_path,
            required_merge_method=ai_status.REQUIRED_REVIEW_MERGE_METHOD,
        )
    except github_review_bridge.GitHubReviewBridgeError as exc:
        raise SystemExit(
            f"GitHub rejected the proposed delivery binding for {task_id or '?'}: {exc}"
        ) from exc
    return dict(validated.as_dict())


def _safe_repo_relative_path(value: Any, *, label: str) -> PurePosixPath:
    raw = str(value or "").strip()
    path = PurePosixPath(raw)
    if (
        not raw
        or raw.startswith("/")
        or "\\" in raw
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise SystemExit(f"{label} must be a normalized repository-relative path")
    return path


def validate_review_manifest_contract_path(
    task: Mapping[str, Any],
    config: dict[str, Any],
    *,
    repository_id: str,
    review_file: str,
) -> str:
    """Require REVIEW_FILE to belong to this task's delivery contract.

    Merely committing some evidence file on the reviewed head is insufficient:
    a reviewer must be bound to an artifact path the task actually authorizes.
    A declared directory-like artifact is an allowed prefix; a declared file is
    accepted exactly. Repository prefixes are removed before comparison.
    """

    task_id = str(task.get("id") or "?").strip()
    manifest = _safe_repo_relative_path(review_file, label="REVIEW_FILE")
    allowed: list[PurePosixPath] = []
    artifacts = task.get("artifacts")
    if isinstance(artifacts, list):
        for artifact in artifacts:
            artifact_repo = artifact_repository_id(
                config,
                artifact,
                default_repo_id=repository_id,
            )
            if artifact_repo != repository_id:
                continue
            relative = repository_relative_artifact_path(
                config,
                artifact,
                repository_id,
            )
            try:
                normalized = _safe_repo_relative_path(
                    relative.as_posix(),
                    label="task artifact",
                )
            except SystemExit:
                continue
            allowed.append(normalized)
    if not any(
        manifest == artifact or artifact in manifest.parents
        for artifact in allowed
    ):
        rendered = ", ".join(path.as_posix() for path in allowed) or "<none>"
        raise SystemExit(
            f"{task_id}: REVIEW_FILE={manifest.as_posix()!r} is outside the task "
            f"artifact contract for repository {repository_id!r}; allowed: {rendered}"
        )
    return manifest.as_posix()


@dataclass(frozen=True)
class OpenPullRequestDiscovery:
    """Typed result that distinguishes confirmed absence from every PR state."""

    pr: int | None
    head_sha: str = ""
    state: str = ""

    @property
    def found(self) -> bool:
        return self.pr is not None


def _discover_open_pull_request_for_branch(
    *, repository: str, head_branch: str, base: str
) -> OpenPullRequestDiscovery:
    """Return one exact PR in any state or a positive confirmation none exists.

    Handoff is the one place delivery identity may be discovered -- review
    must never infer it (see resolve_approval_binding). A task with no
    required_artifacts PR marker and no explicit REVIEW_PR/REVIEW_HEAD_SHA
    still frequently delivers via a real PR in practice; without this check
    its binding silently falls to artifact_contract, and approval never
    reaches GitHub (resolve_approval_binding returns no binding for that
    kind), permanently blocking the PR on branch protection with no way out
    except reopen+re-handoff.

    Only a valid empty GitHub response means ``no pull request``. Command
    failures, invalid payloads, and ambiguous matches fail closed so a real PR
    can never be silently downgraded to an artifact-only review contract.
    """
    ai_status = _ai_status_module()

    owner, _, _ = repository.partition("/")
    if not owner or "/" not in repository:
        raise SystemExit(
            f"Cannot discover pull requests for invalid repository slug {repository!r}"
        )
    try:
        result = subprocess.run(
            [
                "gh", "api", f"repos/{repository}/pulls",
                "--method", "GET",
                "-f", f"head={owner}:{head_branch}",
                "-f", f"base={base}",
                "-f", "state=all",
            ],
            cwd=ai_status.ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SystemExit(
            f"Cannot determine whether {head_branch} has a PR in "
            f"{repository}: {exc}"
        ) from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise SystemExit(
            f"Cannot determine whether {head_branch} has a PR in "
            f"{repository}: gh exited {result.returncode}"
            + (f": {detail}" if detail else "")
        )
    if not result.stdout.strip():
        raise SystemExit(
            f"Cannot determine whether {head_branch} has a PR in "
            f"{repository}: GitHub returned an empty response"
        )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise SystemExit(
            f"Cannot determine whether {head_branch} has a PR in "
            f"{repository}: GitHub returned invalid JSON"
        ) from exc
    if not isinstance(payload, list):
        raise SystemExit(
            f"Cannot determine whether {head_branch} has a PR in "
            f"{repository}: GitHub returned a non-list payload"
        )
    if not payload:
        return OpenPullRequestDiscovery(pr=None)
    if len(payload) != 1:
        raise SystemExit(
            f"Cannot determine the delivery PR for {head_branch} in {repository}: "
            f"GitHub returned {len(payload)} pull requests across all states"
        )
    pr = payload[0]
    if not isinstance(pr, dict):
        raise SystemExit(
            f"Cannot determine the delivery PR for {head_branch} in {repository}: "
            "GitHub returned an invalid pull-request row"
        )
    number = pr.get("number")
    head = pr.get("head")
    head_sha = str(head.get("sha") or "") if isinstance(head, Mapping) else ""
    raw_state = str(pr.get("state") or "").strip().upper()
    state = "MERGED" if pr.get("merged_at") else raw_state
    if (
        not isinstance(number, int)
        or number <= 0
        or not ai_status.APPROVAL_HEAD_SHA_RE.fullmatch(head_sha)
        or state not in {"OPEN", "MERGED", "CLOSED"}
    ):
        raise SystemExit(
            f"Cannot determine the delivery PR for {head_branch} in {repository}: "
            "GitHub returned an incomplete pull-request identity"
        )
    return OpenPullRequestDiscovery(
        pr=number,
        head_sha=head_sha.lower(),
        state=state,
    )


def resolve_handoff_delivery_binding(
    task: Mapping[str, Any], config: dict[str, Any]
) -> dict[str, Any]:
    """Create the one delivery contract before a task becomes reviewable.

    A PR task receives one exact-head identity plus immutable manifest,
    current-base, and merge-method evidence. A genuinely non-PR task retains
    the explicit artifact contract path; it never gains PR merge authority.
    """
    ai_status = _ai_status_module()

    task_id = str(task.get("id") or "").strip()
    try:
        repository_id = validate_task_repository_scope(config, dict(task))
    except (ValueError, RuntimeError) as exc:
        raise SystemExit(
            f"Handoff requires valid repository scope for {task_id or '?'}: {exc}"
        ) from exc

    explicit_pr = bool(os.environ.get("REVIEW_PR", "").strip())
    explicit_head = bool(os.environ.get("REVIEW_HEAD_SHA", "").strip())
    if explicit_pr != explicit_head:
        raise SystemExit(
            "REVIEW_PR and REVIEW_HEAD_SHA must be supplied together for a PR handoff"
        )
    head_branch = (
        os.environ.get("REVIEW_HEAD_BRANCH", "").strip() or f"task/{task_id}"
    )
    base_branch = (
        os.environ.get("REVIEW_BASE", "").strip() or ai_status.DEFAULT_APPROVAL_BASE_BRANCH
    )
    if explicit_pr:
        candidate = _validated_pr_binding(
            {
                "pr": os.environ.get("REVIEW_PR", "").strip().lstrip("#"),
                "head_sha": os.environ.get("REVIEW_HEAD_SHA", "").strip(),
                "head_branch": head_branch,
                "base": base_branch,
            },
            task_id,
        )
        return {
            "kind": "pull_request",
            **validate_handoff_pr_delivery_binding(
                task,
                config,
                candidate,
                review_file=os.environ.get("REVIEW_FILE", ""),
            ),
        }

    if requires_pr_delivery_binding(task):
        raise SystemExit(
            f"{task_id} requires a PR delivery binding at handoff. Set REVIEW_PR "
            "and REVIEW_HEAD_SHA after pushing the delivery branch; historical "
            "source_ref/github metadata is not a reviewable delivery identity."
        )

    repository_slug_value = repository_slug(config, repository_id)
    if not repository_slug_value:
        raise SystemExit(
            f"Handoff cannot determine whether {task_id or '?'} has a PR without "
            "a configured GitHub repository slug"
        )
    discovered = _discover_open_pull_request_for_branch(
        repository=repository_slug_value,
        head_branch=head_branch,
        base=base_branch,
    )
    if discovered.found:
        assert discovered.pr is not None
        return {
            "kind": "pull_request",
            **validate_handoff_pr_delivery_binding(
                task,
                config,
                _validated_pr_binding(
                    {
                        "pr": discovered.pr,
                        "head_sha": discovered.head_sha,
                        "head_branch": head_branch,
                        "base": base_branch,
                    },
                    task_id,
                ),
                review_file=os.environ.get("REVIEW_FILE", ""),
            ),
        }
    contract = _delivery_contract_payload(task)
    return {
        "kind": "artifact_contract",
        **contract,
        "contract_sha256": ai_status._canonical_json_sha256(contract),
    }


def validate_delivery_binding_for_approval(
    task: Mapping[str, Any],
    review_binding: Mapping[str, Any],
) -> None:
    """Reject a review that is not for the delivery frozen at handoff."""
    ai_status = _ai_status_module()

    delivery = task.get(ai_status.DELIVERY_BINDING_KEY)
    if not isinstance(delivery, Mapping):
        raise SystemExit(
            f"{task.get('id') or 'task'} has no handoff delivery binding; reopen "
            "and hand off the current delivery before review."
        )
    kind = str(delivery.get("kind") or "").strip()
    task_id = str(task.get("id") or "").strip()
    if kind == "pull_request":
        expected = _validated_pr_binding(delivery, task_id)
        actual = _validated_pr_binding(review_binding, task_id)
        if actual != expected:
            raise SystemExit(
                f"{task_id} review binding does not match the exact PR head frozen at handoff; "
                "reopen and hand off the new delivery head for review."
            )
        return
    if kind == "artifact_contract":
        expected = dict(delivery)
        contract = _delivery_contract_payload(task)
        if (
            expected.get("contract_sha256") != ai_status._canonical_json_sha256(contract)
            or expected.get("task_id") != contract["task_id"]
        ):
            raise SystemExit(
                f"{task_id} artifact delivery contract changed after handoff; "
                "reopen and hand off the current contract for review."
            )
        return
    raise SystemExit(f"{task_id} has an unknown delivery binding kind: {kind}")


def resolve_approval_binding(
    task: dict[str, Any],
) -> dict[str, Any]:
    """Read the delivery identity frozen at handoff for approval.

    Review never discovers or infers a delivery identity.  That would let a
    reviewer approve one head while later closeout sees a different head.
    Every reviewable task therefore arrives from handoff with one immutable
    PR or artifact contract, and reopening is the only way to replace it.
    """
    ai_status = _ai_status_module()

    task_id = str(task.get("id") or "").strip()
    raw_pr = os.environ.get("REVIEW_PR", "").strip().lstrip("#")
    raw_head = os.environ.get("REVIEW_HEAD_SHA", "").strip()
    base_branch = (
        os.environ.get("REVIEW_BASE", "").strip() or ai_status.DEFAULT_APPROVAL_BASE_BRANCH
    )
    head_branch = (
        os.environ.get("REVIEW_HEAD_BRANCH", "").strip() or f"task/{task_id}"
    )
    if raw_head and not raw_pr:
        raise SystemExit(
            "REVIEW_HEAD_SHA was supplied without REVIEW_PR; both are required."
        )
    if raw_pr and not raw_head:
        raise SystemExit(
            "REVIEW_PR was supplied without REVIEW_HEAD_SHA; both are required."
        )
    if raw_pr and (not raw_pr.isdigit() or int(raw_pr) <= 0):
        raise SystemExit(f"REVIEW_PR must be a positive PR number, got {raw_pr!r}")
    if raw_head and not ai_status.APPROVAL_HEAD_SHA_RE.fullmatch(raw_head):
        raise SystemExit(
            f"REVIEW_HEAD_SHA must be a full 40-hex commit oid, got {raw_head!r}. "
            "An abbreviated sha cannot be compared exactly."
        )

    delivery = task.get(ai_status.DELIVERY_BINDING_KEY)
    if not isinstance(delivery, Mapping):
        raise SystemExit(
            f"{task_id} has no handoff delivery binding; reopen and hand off "
            "the current delivery before approval."
        )
    kind = str(delivery.get("kind") or "").strip()
    if kind == "pull_request":
        persisted = _validated_pr_binding(delivery, task_id)
        if raw_pr or raw_head:
            explicit = _validated_pr_binding(
                {
                    "pr": raw_pr,
                    "head_sha": raw_head,
                    "head_branch": head_branch,
                    "base": base_branch,
                },
                task_id,
            )
            if explicit != persisted:
                raise SystemExit(
                    f"{task_id} supplied review head does not match its handoff delivery binding; "
                    "reopen and hand off the new PR head first."
                )
        return persisted
    if kind == "artifact_contract":
        if raw_pr or raw_head:
            raise SystemExit(
                f"{task_id} is artifact-bound at handoff and cannot receive a PR review binding "
                "without a new handoff."
            )
        return {}
    raise SystemExit(f"{task_id} has an unknown delivery binding kind: {kind}")


def requires_pr_delivery_binding(task: Mapping[str, Any]) -> bool:
    """Whether the current task contract requires a pull-request delivery.

    Historical ``source_ref`` and ``github`` fields are provenance, never a
    future delivery identity.
    """

    required_artifacts = task.get("required_artifacts")
    if not isinstance(required_artifacts, list):
        return False
    for artifact in required_artifacts:
        normalized = " ".join(str(artifact or "").casefold().split())
        if normalized in {"pr", "pull request", "merge sha"}:
            return True
        if "exact-head" in normalized or "pull request" in normalized:
            return True
    return False


def _mapping_has_pull_request_identity(value: Mapping[str, Any]) -> bool:
    ai_status = _ai_status_module()
    kind = str(value.get("kind") or value.get("type") or "").strip().casefold()
    if kind in {"pr", "pull_request", "pull request"}:
        return True
    for key in ai_status._LEGACY_PULL_REQUEST_FIELDS:
        if key in value:
            return True
    for key in ("url", "html_url"):
        if ai_status._PULL_REQUEST_URL_RE.search(str(value.get(key) or "")):
            return True
    return False


def pull_request_delivery_reason(task: Mapping[str, Any]) -> str:
    """Return why a task is known to be PR-backed, including legacy rows."""
    ai_status = _ai_status_module()

    delivery = task.get(ai_status.DELIVERY_BINDING_KEY)
    if isinstance(delivery, Mapping) and str(delivery.get("kind") or "") == "pull_request":
        return ai_status.DELIVERY_BINDING_KEY
    if requires_pr_delivery_binding(task):
        return "required_artifacts"
    for key in (
        ai_status.APPROVAL_BINDING_KEY,
        ai_status.GITHUB_REVIEW_BRIDGE_KEY,
        ai_status.OPERATOR_ACCEPTANCE_KEY,
    ):
        value = task.get(key)
        if isinstance(value, Mapping) and _mapping_has_pull_request_identity(value):
            return key
    for key in (
        "source_ref",
        "github",
        "delivery",
        "completion_evidence",
        "external_delivery",
    ):
        value = task.get(key)
        if isinstance(value, Mapping) and _mapping_has_pull_request_identity(value):
            return key
    return ""


def _legacy_delivery_branch_pair(task: Mapping[str, Any]) -> tuple[str, str]:
    ai_status = _ai_status_module()
    task_id = str(task.get("id") or "").strip()
    for key in (
        ai_status.DELIVERY_BINDING_KEY,
        ai_status.APPROVAL_BINDING_KEY,
        ai_status.GITHUB_REVIEW_BRIDGE_KEY,
        ai_status.OPERATOR_ACCEPTANCE_KEY,
        "github",
        "source_ref",
        "delivery",
        "completion_evidence",
        "external_delivery",
    ):
        value = task.get(key)
        if not isinstance(value, Mapping):
            continue
        head_branch = str(
            value.get("head_branch")
            or value.get("branch")
            or value.get("headRefName")
            or ""
        ).strip()
        base = str(
            value.get("base") or value.get("baseRefName") or ""
        ).strip()
        if head_branch:
            return head_branch, base or ai_status.DEFAULT_APPROVAL_BASE_BRANCH
    return f"task/{task_id}", ai_status.DEFAULT_APPROVAL_BASE_BRANCH


def review_gate_delivery_kind(
    task: Mapping[str, Any], config: dict[str, Any]
) -> tuple[str, str]:
    """Classify review/closeout delivery without downgrading uncertain PRs."""
    ai_status = _ai_status_module()

    delivery = task.get(ai_status.DELIVERY_BINDING_KEY)
    current_binding = task_machine.delivery_binding_is_current(task)
    current_artifact_binding = (
        current_binding
        and isinstance(delivery, Mapping)
        and str(delivery.get("kind") or "") == "artifact_contract"
    )
    if (
        current_binding
        and isinstance(delivery, Mapping)
        and str(delivery.get("kind") or "") == "pull_request"
    ):
        return "pull_request", ai_status.DELIVERY_BINDING_KEY

    if (
        operator_acceptance_evidence_matches(task)
        and isinstance(delivery, Mapping)
        and str(delivery.get("kind") or "") == "pull_request"
    ):
        return "pull_request", ai_status.OPERATOR_ACCEPTANCE_KEY

    # A review row without a complete current handoff binding is never safe to
    # reinterpret as artifact-only. Legacy provenance may explain why it is a
    # PR row, but cannot manufacture the frozen admission facts review needs.
    status = str(task.get("status") or "").strip().lower()
    if not current_binding and status in {"review", "review_approved"}:
        raise SystemExit(
            f"{task.get('id') or 'task'} has unknown legacy delivery identity; "
            "reopen and re-handoff a current PR or artifact contract before review"
        )

    # A current artifact contract is the delivery identity established by the
    # latest handoff. Historical source_ref/github/review mappings remain
    # provenance and cannot replace it. The current task contract can still
    # require PR delivery, and state=all discovery below must still catch a PR
    # that appeared after the artifact handoff.
    if requires_pr_delivery_binding(task):
        reason = "required_artifacts"
    elif current_artifact_binding:
        # A canonical approval/bridge mapping is not generic provenance: if
        # one somehow coexists with an artifact handoff, fail closed as an
        # inconsistent PR review row. New handoff normally clears both.
        reason = next(
            (
                key
                for key in (
                    ai_status.APPROVAL_BINDING_KEY,
                    ai_status.GITHUB_REVIEW_BRIDGE_KEY,
                    ai_status.OPERATOR_ACCEPTANCE_KEY,
                )
                if isinstance(task.get(key), Mapping)
                and _mapping_has_pull_request_identity(task[key])
            ),
            "",
        )
    else:
        reason = pull_request_delivery_reason(task)
    if reason:
        return "pull_request", reason

    task_id = str(task.get("id") or "").strip()
    try:
        repository_id = validate_task_repository_scope(config, dict(task))
    except (ValueError, RuntimeError) as exc:
        raise SystemExit(
            f"Cannot determine delivery kind for {task_id or '?'}: {exc}"
        ) from exc
    repository_slug_value = repository_slug(config, repository_id)
    if not repository_slug_value:
        raise SystemExit(
            f"Cannot determine delivery kind for {task_id or '?'} without a "
            "configured GitHub repository slug"
        )
    head_branch, base = _legacy_delivery_branch_pair(task)
    discovered = _discover_open_pull_request_for_branch(
        repository=repository_slug_value,
        head_branch=head_branch,
        base=base,
    )
    if discovered.found:
        return "pull_request", f"{discovered.state.lower()}_pull_request"
    if current_artifact_binding:
        return "artifact_contract", ai_status.DELIVERY_BINDING_KEY
    return "artifact_contract", "confirmed_no_pull_request"


def require_current_pr_delivery_binding(
    task: Mapping[str, Any], *, action: str
) -> Mapping[str, Any]:
    """Require the complete handoff-frozen PR and manifest for review/closeout."""
    ai_status = _ai_status_module()

    task_id = str(task.get("id") or "task").strip()
    delivery = task.get(ai_status.DELIVERY_BINDING_KEY)
    if (
        not isinstance(delivery, Mapping)
        or str(delivery.get("kind") or "") != "pull_request"
        or not task_machine.delivery_binding_is_current(task)
    ):
        raise SystemExit(
            f"{task_id} has legacy or incomplete PR delivery evidence and cannot "
            f"{action}; reopen and re-handoff the exact PR head, current base, "
            "merge method, and evidence manifest first"
        )
    manifest = delivery.get("evidence_manifest")
    assert isinstance(manifest, Mapping)
    frozen_review_file = str(manifest.get("path") or "").strip()
    task_review_file = str(task.get("review_file") or "").strip()
    if not task_review_file or task_review_file != frozen_review_file:
        raise SystemExit(
            f"{task_id} has legacy or incomplete PR evidence-manifest binding and "
            f"cannot {action}; reopen and re-handoff the exact PR delivery first"
        )
    return delivery


def resolve_operator_accept_delivery_binding(
    task: Mapping[str, Any],
    config: dict[str, Any],
    repository_slug_value: str,
) -> dict[str, Any]:
    """Resolve or rehabilitate a PR delivery binding for operator acceptance.

    If a full, current PR delivery binding is present and matches the task's
    review_file, it is used. Otherwise (legacy PR handoff lacking evidence
    manifest or incomplete binding), only explicit Human/Ops operator_accept
    can rehabilitate the minimal PR delivery binding by verifying the PR's
    exact head and base ancestry directly with GitHub in real time.
    """
    ai_status = _ai_status_module()
    task_id = str(task.get("id") or "").strip()
    raw_pr = os.environ.get("REVIEW_PR", "").strip().lstrip("#")
    raw_head = os.environ.get("REVIEW_HEAD_SHA", "").strip()
    if (raw_pr and not raw_head) or (raw_head and not raw_pr):
        raise SystemExit(
            "REVIEW_PR and REVIEW_HEAD_SHA must be supplied together for operator acceptance"
        )
    if raw_pr and (not raw_pr.isdigit() or int(raw_pr) <= 0):
        raise SystemExit(f"REVIEW_PR must be a positive PR number, got {raw_pr!r}")
    if raw_head and not ai_status.APPROVAL_HEAD_SHA_RE.fullmatch(raw_head):
        raise SystemExit(
            f"REVIEW_HEAD_SHA must be a full 40-hex commit oid, got {raw_head!r}. "
            "An abbreviated sha cannot be compared exactly."
        )

    current_delivery = task.get(ai_status.DELIVERY_BINDING_KEY)
    is_current_pr = (
        isinstance(current_delivery, Mapping)
        and str(current_delivery.get("kind") or "") == "pull_request"
        and task_machine.delivery_binding_is_current(task)
    )
    manifest = current_delivery.get("evidence_manifest") if is_current_pr else None
    manifest_path = (
        str(manifest.get("path") or "").strip()
        if isinstance(manifest, Mapping)
        else ""
    )
    task_review_file = str(task.get("review_file") or "").strip()
    has_full_manifest = (
        is_current_pr
        and bool(manifest_path)
        and (not task_review_file or task_review_file == manifest_path)
    )

    if not raw_pr and not raw_head and has_full_manifest:
        assert isinstance(current_delivery, Mapping)
        return dict(current_delivery)

    head_branch = (
        os.environ.get("REVIEW_HEAD_BRANCH", "").strip() or f"task/{task_id}"
    )
    base_branch = (
        os.environ.get("REVIEW_BASE", "").strip() or ai_status.DEFAULT_APPROVAL_BASE_BRANCH
    )
    if raw_pr and raw_head:
        candidate = _validated_pr_binding(
            {
                "pr": int(raw_pr),
                "head_sha": raw_head,
                "head_branch": head_branch,
                "base": base_branch,
            },
            task_id,
        )
    else:
        candidate = None
        for key in (
            ai_status.DELIVERY_BINDING_KEY,
            ai_status.APPROVAL_BINDING_KEY,
            ai_status.GITHUB_REVIEW_BRIDGE_KEY,
            ai_status.OPERATOR_ACCEPTANCE_KEY,
            "github",
            "source_ref",
            "delivery",
            "completion_evidence",
            "external_delivery",
        ):
            value = task.get(key)
            if not isinstance(value, Mapping):
                continue
            pr_val = (
                value.get("pr")
                or value.get("pr_number")
                or value.get("number")
                or value.get("pull_request")
                or value.get("primary_pr")
                or value.get("implementation_pr")
                or value.get("pr_url")
                or value.get("pull_request_url")
                or value.get("url")
            )
            head_val = (
                value.get("head_sha")
                or value.get("commit")
                or value.get("sha")
                or value.get("headRefOid")
                or value.get("primary_merge_commit")
                or value.get("implementation_commit")
                or value.get("merge_commit")
            )
            branch_val = (
                value.get("head_branch")
                or value.get("branch")
                or value.get("headRefName")
            )
            base_val = value.get("base") or value.get("baseRefName")
            if pr_val and head_val:
                try:
                    candidate = _validated_pr_binding(
                        {
                            "pr": pr_val,
                            "head_sha": head_val,
                            "head_branch": branch_val or head_branch,
                            "base": base_val or base_branch,
                        },
                        task_id,
                    )
                    break
                except SystemExit:
                    continue

        if candidate is None:
            head_branch, base_branch = _legacy_delivery_branch_pair(task)
            discovered = _discover_open_pull_request_for_branch(
                repository=repository_slug_value,
                head_branch=head_branch,
                base=base_branch,
            )
            if discovered.found and discovered.pr and discovered.head_sha:
                candidate = _validated_pr_binding(
                    {
                        "pr": discovered.pr,
                        "head_sha": discovered.head_sha,
                        "head_branch": head_branch,
                        "base": base_branch,
                    },
                    task_id,
                )

    if candidate is None:
        raise SystemExit(
            f"Cannot operator-accept task {task_id}: no declared PR identity found; "
            "set REVIEW_PR and REVIEW_HEAD_SHA"
        )

    github_review_bridge = ai_status._github_review_bridge_module()
    try:
        admission = github_review_bridge.rehabilitate_operator_admission(
            repository=repository_slug_value,
            binding=candidate,
            required_merge_method=ai_status.REQUIRED_REVIEW_MERGE_METHOD,
            allow_base_advance=True,
            frozen_base_sha=str((task.get(ai_status.DELIVERY_BINDING_KEY) or {}).get("base_sha") or "").strip().lower(),
        )
    except github_review_bridge.ReviewBindingMismatch as exc:
        raise SystemExit(
            f"GitHub rejected operator acceptance for {task_id}: {exc}"
        ) from exc
    except github_review_bridge.GitHubReviewBridgeError as exc:
        raise SystemExit(
            f"GitHub rejected operator acceptance for {task_id}: {exc}"
        ) from exc

    return {
        "kind": "pull_request",
        "pr": admission.pr,
        "head_sha": admission.head_sha,
        "head_branch": admission.head_branch,
        "base": admission.base,
        "base_sha": admission.base_sha,
        "required_merge_method": admission.required_merge_method,
    }


def operator_acceptance_evidence_matches(task: Mapping[str, Any]) -> bool:
    """Return whether a Human/Ops acceptance proves this exact PR head.

    This does not reuse reviewer evidence: the two paths have different
    authority and must remain visibly distinguishable in canonical state.
    """
    ai_status = _ai_status_module()

    binding = task.get(ai_status.APPROVAL_BINDING_KEY)
    evidence = task.get(ai_status.OPERATOR_ACCEPTANCE_KEY)
    if not isinstance(binding, Mapping) or not isinstance(evidence, Mapping):
        return False
    # Operator acceptance records frozen-base evidence from the immutable PR
    # delivery binding.  The review binding intentionally contains only the
    # PR identity used by ordinary reviewer workflows, so pass the frozen base
    # through from the delivery binding without relaxing any of the shared
    # PR/head/branch/base equality checks.
    bridge_binding = dict(binding)
    delivery = task.get(ai_status.DELIVERY_BINDING_KEY)
    if isinstance(delivery, Mapping):
        for field in ("pr", "head_sha", "head_branch", "base"):
            if str(delivery.get(field) or "").strip() != str(
                binding.get(field) or ""
            ).strip():
                return False
        frozen_base_sha = str(delivery.get("base_sha") or "").strip().lower()
        if frozen_base_sha:
            bridge_binding["base_sha"] = frozen_base_sha
    bridge = ai_status._github_review_bridge_module()
    try:
        bridge.validate_operator_acceptance_evidence(
            evidence,
            repository=str(evidence.get("repository") or "").strip(),
            actor="Human/Ops",
            binding=bridge_binding,
            intent_nonce=(
                str(evidence.get("intent_nonce") or "").strip() or None
            ),
        )
    except bridge.GitHubReviewBridgeError:
        return False
    return True


def exact_head_acceptance_evidence_matches(task: Mapping[str, Any]) -> bool:
    """Accept either independent review or the explicit operator path."""

    return (
        github_review_bridge_evidence_matches(task)
        or operator_acceptance_evidence_matches(task)
    )
