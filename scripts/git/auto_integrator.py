#!/usr/bin/env python3
"""Conservative serialized integrator for canonical task PRs.

The auto-integrator closes the gap between a task becoming integration-eligible
and its PR actually landing in `dev`. Eligibility means either an exact
`review_approved` task or an active task whose canonical contract explicitly
permits merge-then-review. It is intentionally narrow:

* one process at a time via `.orchestrator/auto-integrator.lock`;
* only `review_approved` tasks with `task/<TASK-ID>` PRs into `dev`;
* no conflict resolution;
* no branch-protection bypass;
* unblock tasks instead of stranded PRs when the safe path fails.

Merge authority is delegated to `task_review_merge_gate`.  For a task whose
canonical contract requires independent review the integrator merges only the
exact head the assigned reviewer approved, never enables GitHub auto-merge,
never force-pushes a rebase over the reviewed head, and actively revokes an
auto-merge request it finds on an unapproved gated PR.  Tasks whose canonical
contract permits merge-then-review are held to the same exact-head checks,
smoke validation, and synchronous merge authority.

The default mode is dry-run. Pass `--execute` to mutate git/GitHub/task state.
"""

from __future__ import annotations

import argparse
import errno
import fcntl
import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
import hashlib
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence


sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / ".orchestrator"))

import task_review_merge_gate as review_gate  # noqa: E402  (local helper module)
import github_review_bridge  # noqa: E402  (local helper module)
import multi_repo_registry  # noqa: E402  (orchestrator module)
import common as orchestrator_common  # noqa: E402  (canonical lock helpers)
from rewrite import integration_receipt  # noqa: E402  (DTG-INT-01 canonical receipt authority)


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / ".orchestrator" / "config.json"
DEFAULT_STATUS = ROOT / "ai-status.json"
DEFAULT_DEV_BRANCH = "dev"
DEFAULT_TASK_PREFIX = "task/"
DISPOSABLE_MERGE_IDENTITY = (
    "-c",
    "user.name=Pantheon Auto Integrator",
    "-c",
    "user.email=pantheon-auto-integrator@noreply.local",
)
DEFAULT_LOCK = ".orchestrator/auto-integrator.lock"
DEFAULT_MERGE_METHOD = "merge"
UNBLOCK_REQUEST_SCHEMA = "pantheon-auto-integrator-unblock-request/v1"
UNBLOCK_REQUEST_INBOX = ".orchestrator/auto-integrator-unblock-inbox"
DEFAULT_LIVE_CONFIG = Path(
    "/home/lupin/pantheon-ci-deploy/runtime/live-supervisor-mainroot-config.json"
)
LIVE_CONFIG_ENV = "PANTHEON_LIVE_SUPERVISOR_CONFIG"
FINAL_MERGE_TIMEOUT_SECONDS = 60.0
LOCK_SCHEMA = "pantheon-auto-integrator-lock/v2"
SUCCESS_VALUES = {"SUCCESS", "SUCCESSFUL", "PASSED", "PASS", "SKIPPED", "NEUTRAL"}
PENDING_VALUES = {
    "PENDING",
    "QUEUED",
    "IN_PROGRESS",
    "REQUESTED",
    "WAITING",
    "EXPECTED",
}
FAILURE_VALUES = {
    "FAILURE",
    "FAILED",
    "ERROR",
    "CANCELLED",
    "CANCELED",
    "TIMED_OUT",
    "TIMEOUT",
    "ACTION_REQUIRED",
    "STARTUP_FAILURE",
}
IGNORABLE_DIAGNOSTIC_WORKFLOWS = frozenset({"Canonical Review Attestation Audit"})
ALLOWED_PRE_REBASE_MERGE_STATES = {"CLEAN", "HAS_HOOKS", "BEHIND", "UNSTABLE", "UNKNOWN"}
ALLOWED_DIRECT_MERGE_STATES = {"CLEAN", "HAS_HOOKS", "UNSTABLE", "UNKNOWN"}
PR_DETAIL_FIELDS = (
    "number,title,url,headRefName,headRefOid,baseRefName,isDraft,mergeStateStatus,"
    "reviewDecision,statusCheckRollup,state,mergeCommit,mergedAt,commits,"
    "autoMergeRequest"
)


@dataclass(frozen=True)
class Settings:
    dev_branch: str = DEFAULT_DEV_BRANCH
    task_branch_prefix: str = DEFAULT_TASK_PREFIX
    lock_path: Path = ROOT / DEFAULT_LOCK
    max_tasks_per_run: int = 1
    smoke_commands: tuple[str, ...] = ()
    unblock_owner: str | None = None
    unblock_reviewer: str | None = None


@dataclass(frozen=True)
class TaskCandidate:
    task_id: str
    title: str
    owner: str
    reviewer: str
    branch: str
    repository_id: str = "pantheon"
    repository_slug: str = "ajoe734/pantheon"
    repository_root: Path = ROOT
    target_branch: str = DEFAULT_DEV_BRANCH
    dedicated_integration_path: bool = False
    scope_error: str | None = None
    raw_task: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CheckSummary:
    state: str
    total: int = 0
    failing: tuple[str, ...] = ()
    pending: tuple[str, ...] = ()
    ignored_diagnostic: tuple[str, ...] = ()


@dataclass
class IntegrationResult:
    task_id: str
    action: str
    detail: str
    pr_number: int | None = None
    pr_url: str = ""
    unblock_task_id: str | None = None
    dry_run: bool = True
    commands: list[list[str]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "action": self.action,
            "detail": self.detail,
            "pr_number": self.pr_number,
            "pr_url": self.pr_url,
            "unblock_task_id": self.unblock_task_id,
            "dry_run": self.dry_run,
            "commands": self.commands,
        }


@dataclass(frozen=True)
class ReviewGate:
    """Canonical review-before-merge authority for one integration pass.

    Production runs read the bound status root. Tests inject the canonical
    task rows and audit events directly so the fail-closed rules can be proven
    without GitHub or a status root on disk.
    """

    status_root: Path = ROOT
    state: Mapping[str, Any] | None = None
    events: Sequence[Mapping[str, Any]] | None = None

    def decide(
        self,
        candidate: "TaskCandidate",
        pr: Mapping[str, Any] | None,
        settings: "Settings",
        *,
        task_brief_carry_forward: Mapping[str, Any] | None = None,
    ) -> review_gate.GateDecision:
        return review_gate.gate_for_task(
            candidate.task_id,
            pr,
            status_root=self.status_root,
            dev_branch=candidate.target_branch,
            task_branch_prefix=settings.task_branch_prefix,
            state=self.state,
            events=self.events,
            task_brief_carry_forward=task_brief_carry_forward,
        )

    def task_brief_carry_forward(
        self,
        candidate: "TaskCandidate",
        pr: Mapping[str, Any] | None,
        runner: "CommandRunner",
        *,
        root: Path,
    ) -> dict[str, Any] | None:
        """Classify the one generated-brief successor exception without writes."""

        if not isinstance(pr, Mapping):
            return None
        head_sha = str(pr.get("headRefOid") or "").strip().lower()
        repository = (
            github_review_bridge.repository_from_pull_request_url(pr.get("url"))
            or candidate.repository_slug
        )
        if not repository:
            return None
        try:
            contract = review_gate.load_task_contract(
                candidate.task_id,
                status_root=self.status_root,
                state=self.state,
            )
            if contract.policy != review_gate.POLICY_REVIEW_BEFORE_MERGE:
                return None
            approval = review_gate.load_approval_record(
                candidate.task_id,
                status_root=self.status_root,
                events=self.events,
            )
        except review_gate.TaskReviewGateError:
            return None
        if (
            approval is None
            or not approval.present
            or approval.revoked
            or not approval.binding_present
            or approval.binding_error
            or approval.approved_head_sha == head_sha
            or review_gate.normalize_agent(approval.reviewer)
            != review_gate.normalize_agent(contract.reviewer)
            or review_gate.normalize_pr_number(pr.get("number")) != approval.approved_pr_number
            or str(pr.get("headRefName") or "").strip() != approval.approved_head_branch
            or str(pr.get("baseRefName") or "").strip() != approval.approved_base_branch
        ):
            return None
        try:
            return github_review_bridge.task_brief_only_successor(
                repository=repository,
                approved_head_sha=approval.approved_head_sha,
                successor_head_sha=head_sha,
                runner=GitHubJsonCommandRunner(runner, root=root),
            )
        except github_review_bridge.GitHubReviewBridgeError:
            return None

    def publish_task_brief_carry_forward(
        self,
        candidate: "TaskCandidate",
        pr: Mapping[str, Any],
        runner: "CommandRunner",
        *,
        root: Path,
        carried: Mapping[str, Any] | None,
        decision: review_gate.GateDecision,
        dispatch_if_proof_exists: bool = True,
    ) -> dict[str, Any] | None:
        """Publish the proof only after the complete gate allowed this head.

        A prior attempt can leave the proof tag durable while the workflow
        dispatch has not happened yet.  The caller therefore asks for an
        existing proof to be dispatched again until the required canonical
        check has turned green.
        """

        if (
            not decision.allow_merge
            or decision.reason != "task_brief_only_approval_carried_forward"
            or not isinstance(carried, Mapping)
        ):
            return None
        repository = (
            github_review_bridge.repository_from_pull_request_url(pr.get("url"))
            or candidate.repository_slug
        )
        actor = str(
            decision.approval.get("reviewer") or decision.contract.get("reviewer") or ""
        ).strip()
        if not repository or not actor:
            raise AutoIntegratorError(
                "task-brief carry-forward was gate-approved but lacks a publishable repository or reviewer"
            )
        try:
            return github_review_bridge.publish_task_brief_only_successor_proof(
                repository=repository,
                task_id=candidate.task_id,
                actor=actor,
                carried=carried,
                pr=review_gate.normalize_pr_number(pr.get("number")) or 0,
                head_branch=str(pr.get("headRefName") or "").strip(),
                base=str(pr.get("baseRefName") or "").strip(),
                dispatch_if_proof_exists=dispatch_if_proof_exists,
                runner=GitHubJsonCommandRunner(runner, root=root),
            )
        except github_review_bridge.GitHubReviewBridgeError as exc:
            raise AutoIntegratorError(f"task-brief carry-forward proof publication failed: {exc}") from exc


class AutoIntegratorError(RuntimeError):
    """Base auto-integrator failure."""


class IntegrationLockError(AutoIntegratorError):
    """The canonical integration lock cannot be used safely."""


class IntegrationLockHeld(IntegrationLockError):
    """Another live canonical integration runner owns the lock."""


class FinalMergeRevalidationError(AutoIntegratorError):
    """Live canonical state or PR state changed after smoke validation."""

    def __init__(self, reason: str, detail: str, *, waiting: bool = False) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail
        self.waiting = waiting


class ExecuteAuthorityError(AutoIntegratorError):
    """The live runner is not bound to its promoted runtime/config identity."""


class AmbiguousPullRequests(AutoIntegratorError):
    """More than one open PR claims the same exact task branch."""

    def __init__(self, branch: str, numbers: Sequence[Any]) -> None:
        rendered = ", ".join(f"#{number}" for number in numbers)
        super().__init__(f"multiple open PRs claim {branch}: {rendered}")
        self.branch = branch
        self.numbers = list(numbers)


class CommandFailure(AutoIntegratorError):
    def __init__(self, args: Sequence[str] | str, returncode: int, output: str = "") -> None:
        rendered = args if isinstance(args, str) else " ".join(args)
        super().__init__(f"command failed ({returncode}): {rendered}\n{output.strip()}")
        self.args_rendered = rendered
        self.returncode = returncode
        self.output = output


class CommandRunner:
    def __init__(self) -> None:
        self.commands: list[list[str]] = []
        self.default_timeout: float | None = None

    def run(
        self,
        args: Sequence[str],
        *,
        cwd: Path = ROOT,
        check: bool = True,
        env: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        command = [str(arg) for arg in args]
        self.commands.append(command)
        try:
            result = subprocess.run(
                command,
                cwd=str(cwd),
                env=dict(env) if env is not None else None,
                capture_output=True,
                text=True,
                timeout=timeout if timeout is not None else self.default_timeout,
            )
        except subprocess.TimeoutExpired:
            raise
        except OSError as exc:
            if check:
                raise CommandFailure(command, 127, str(exc)) from exc
            return subprocess.CompletedProcess(command, 127, "", str(exc))
        if check and result.returncode != 0:
            raise CommandFailure(command, result.returncode, result.stderr or result.stdout)
        return result

    def run_shell(
        self,
        command: str,
        *,
        cwd: Path,
        check: bool = True,
        env: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        self.commands.append(["sh", "-lc", command])
        try:
            result = subprocess.run(
                command,
                cwd=str(cwd),
                shell=True,
                env=dict(env) if env is not None else None,
                capture_output=True,
                text=True,
                timeout=timeout if timeout is not None else self.default_timeout,
            )
        except subprocess.TimeoutExpired:
            raise
        except OSError as exc:
            if check:
                raise CommandFailure(["sh", "-lc", command], 127, str(exc)) from exc
            return subprocess.CompletedProcess(["sh", "-lc", command], 127, "", str(exc))
        if check and result.returncode != 0:
            raise CommandFailure(command, result.returncode, result.stderr or result.stdout)
        return result


@contextmanager
def bounded_runner_timeout(
    runner: CommandRunner, timeout_seconds: float
) -> Iterator[None]:
    previous = runner.default_timeout
    runner.default_timeout = timeout_seconds
    try:
        yield
    finally:
        runner.default_timeout = previous


class GitHubJsonCommandRunner:
    """Adapt the integrator's recorded command runner to the bridge protocol."""

    def __init__(self, command_runner: CommandRunner, *, root: Path) -> None:
        self.command_runner = command_runner
        self.root = root

    def run_json(
        self,
        args: Sequence[str],
        *,
        payload: Mapping[str, Any] | None = None,
    ) -> Any:
        try:
            command = [str(arg) for arg in args]
            if payload is None:
                result = self.command_runner.run(command, cwd=self.root)
            else:
                try:
                    input_index = command.index("--input")
                    if command[input_index + 1] != "-":
                        raise ValueError("expected gh api --input - for JSON payload")
                except (ValueError, IndexError) as exc:
                    raise github_review_bridge.GitHubReviewBridgeError(
                        "GitHub bridge write command must use gh api --input -"
                    ) from exc
                # CommandRunner intentionally records only argv.  Materialize
                # the tiny request body in a private temporary file so its
                # normal subprocess path can give gh the same JSON body that
                # GhJsonRunner would write to stdin.
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    suffix=".json",
                ) as input_file:
                    json.dump(payload, input_file, ensure_ascii=False)
                    input_file.flush()
                    command[input_index + 1] = input_file.name
                    result = self.command_runner.run(command, cwd=self.root)
            text = (result.stdout or "").strip()
            return json.loads(text) if text else None
        except (CommandFailure, json.JSONDecodeError) as exc:
            raise github_review_bridge.GitHubReviewBridgeError(str(exc)) from exc


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return default
    return json.loads(text)


def load_settings(path: Path | None = None, *, status_root: Path | None = None) -> Settings:
    if path is None:
        root = review_gate.resolve_status_root(status_root)
        status_config = root / ".orchestrator" / "config.json"
        path = status_config if status_config.exists() else DEFAULT_CONFIG
    config = load_json(path, {})
    if not isinstance(config, dict):
        config = {}
    workflow = config.get("branch_workflow") or config.get("wave_workflow") or {}
    if not isinstance(workflow, dict):
        workflow = {}
    auto = workflow.get("auto_integrator") or config.get("auto_integrator") or {}
    if not isinstance(auto, dict):
        auto = {}

    dev_branch = str(auto.get("dev_branch") or workflow.get("dev_branch") or DEFAULT_DEV_BRANCH)
    task_prefix = str(auto.get("task_branch_prefix") or workflow.get("task_branch_prefix") or DEFAULT_TASK_PREFIX)
    raw_lock = str(auto.get("lock_file") or DEFAULT_LOCK)
    lock_path = Path(raw_lock)
    if not lock_path.is_absolute():
        root = review_gate.resolve_status_root(status_root)
        if (root / ".orchestrator").exists():
            lock_path = root / lock_path
        else:
            lock_path = ROOT / lock_path
    smoke = auto.get("smoke_commands") or ()
    if isinstance(smoke, str):
        smoke_commands = (smoke,)
    else:
        smoke_commands = tuple(str(item) for item in smoke if str(item).strip())
    merge_method = str(auto.get("merge_method") or DEFAULT_MERGE_METHOD).strip().lower()
    if merge_method != DEFAULT_MERGE_METHOD:
        raise ValueError(
            "Governed auto integration requires merge commits; "
            "squash and rebase merges do not preserve the reviewed head"
        )
    return Settings(
        dev_branch=dev_branch,
        task_branch_prefix=task_prefix,
        lock_path=lock_path,
        max_tasks_per_run=int(auto.get("max_tasks_per_run") or 1),
        smoke_commands=smoke_commands,
        unblock_owner=str(auto.get("unblock_owner") or "").strip() or None,
        unblock_reviewer=str(auto.get("unblock_reviewer") or "").strip() or None,
    )


def resolve_execute_authority(
    live_config_path: Path,
    runner: CommandRunner,
    *,
    command_root: Path = ROOT,
) -> tuple[Path, Path, Settings, dict[str, Any]]:
    """Bind live execution to one promoted command runtime and status plane."""

    requested = live_config_path.expanduser().absolute()
    if requested.is_symlink() or not requested.is_file():
        raise ExecuteAuthorityError(
            f"live supervisor config must be a regular non-symlink file: {requested}"
        )
    config_path = requested.resolve()
    try:
        payload = load_json(config_path, {})
    except (OSError, json.JSONDecodeError) as exc:
        raise ExecuteAuthorityError(f"cannot read live supervisor config: {exc}") from exc
    if not isinstance(payload, dict):
        raise ExecuteAuthorityError("live supervisor config must be a JSON object")

    watchdog = payload.get("watchdog")
    command = watchdog.get("supervisor_command") if isinstance(watchdog, Mapping) else None
    if not isinstance(command, list) or not all(isinstance(item, str) for item in command):
        raise ExecuteAuthorityError("live config watchdog.supervisor_command is missing")
    supervisor_entries = [
        Path(item) for item in command if Path(item).name == "supervisor.py"
    ]
    if len(supervisor_entries) != 1 or not supervisor_entries[0].is_absolute():
        raise ExecuteAuthorityError(
            "live watchdog must name exactly one absolute supervisor.py"
        )
    promoted_root = supervisor_entries[0].parent.parent.resolve()
    actual_root = command_root.resolve()
    expected_supervisor = actual_root / ".orchestrator" / "supervisor.py"
    if promoted_root != actual_root or supervisor_entries[0].resolve() != expected_supervisor:
        raise ExecuteAuthorityError(
            f"auto-integrator command root is not the promoted watchdog root ({actual_root} != {promoted_root})"
        )
    config_indexes = [index for index, item in enumerate(command) if item == "--config"]
    if len(config_indexes) != 1 or config_indexes[0] + 1 >= len(command):
        raise ExecuteAuthorityError("live watchdog must bind exactly one --config path")
    watchdog_config = Path(command[config_indexes[0] + 1]).expanduser()
    if not watchdog_config.is_absolute() or watchdog_config.resolve() != config_path:
        raise ExecuteAuthorityError(
            "auto-integrator live config differs from watchdog --config authority"
        )

    head_proc = runner.run(["git", "rev-parse", "HEAD"], cwd=actual_root, check=False)
    head = head_proc.stdout.strip().lower()
    if (
        head_proc.returncode != 0
        or not review_gate.OID_RE.fullmatch(head)
        or actual_root.name.lower() != head
    ):
        raise ExecuteAuthorityError(
            f"promoted command runtime must be versioned as command-runtimes/<HEAD> ({actual_root.name} != {head or 'missing'})"
        )

    paths = payload.get("paths")
    raw_status = paths.get("status_file") if isinstance(paths, Mapping) else None
    status_file = Path(str(raw_status or "")).expanduser()
    if not status_file.is_absolute() or status_file.name != "ai-status.json":
        raise ExecuteAuthorityError(
            "live config paths.status_file must be an absolute canonical ai-status.json"
        )
    if status_file.is_symlink() or not status_file.is_file():
        raise ExecuteAuthorityError(
            f"canonical status file must be a regular non-symlink file: {status_file}"
        )
    status_file = status_file.resolve()
    status_root = status_file.parent
    settings = load_settings(config_path, status_root=status_root)
    canonical_lock = (status_root / DEFAULT_LOCK).resolve()
    if settings.lock_path.resolve() != canonical_lock:
        raise ExecuteAuthorityError(
            f"live auto-integrator lock must be canonical ({settings.lock_path} != {canonical_lock})"
        )
    return status_file, status_root, settings, payload


def integration_candidates(
    state: Mapping[str, Any],
    *,
    config: Mapping[str, Any] | None = None,
    task_branch_prefix: str = DEFAULT_TASK_PREFIX,
    only_task_id: str | None = None,
    status_root: Path | None = None,
) -> list[TaskCandidate]:
    """Select rows that the canonical runner may evaluate under its lock.

    Review-before-merge rows enter only after exact-head approval. The legacy
    merge-then-review lane enters while active because its PR helper no longer
    grants merge authority directly. Policy resolution here is only a narrow
    admission filter; ``ReviewGate`` resolves the same canonical row again
    against the live PR immediately before any merge operation.
    """

    tasks = state.get("tasks") if isinstance(state.get("tasks"), list) else []
    candidates: list[tuple[int, TaskCandidate]] = []
    config_dict = dict(config) if isinstance(config, Mapping) else {}
    resolved_status_root = review_gate.resolve_status_root(status_root)
    paths = dict(config_dict.get("paths") or {})
    paths["status_file"] = str((resolved_status_root / "ai-status.json").resolve())
    config_dict["paths"] = paths

    for raw in tasks:
        if not isinstance(raw, Mapping):
            continue
        task_id = str(raw.get("id") or "").strip()
        if not task_id or (only_task_id and task_id != only_task_id):
            continue
        status = str(raw.get("status") or "").strip().lower()
        is_review_approved = status == "review_approved"
        contract = review_gate.contract_from_task_row(raw)
        is_active_merge_then_review = (
            status in {"in_progress", "review"}
            and contract.policy == review_gate.POLICY_MERGE_THEN_REVIEW
            and contract.declaration_honored
        )
        if not (is_review_approved or is_active_merge_then_review):
            continue
        # DTG-INT-01: a row already carrying a matching integration_receipt
        # for its current identity has already landed; skip it before any
        # GitHub/ancestry work so the cron stops re-evaluating it forever.
        if integration_receipt.integration_receipt_consumes_candidate(raw):
            continue
        owner = str(raw.get("owner") or "").strip()
        reviewer = str(raw.get("reviewer") or "").strip()
        if not owner or not reviewer:
            continue

        scope_error = None
        repo_id = "pantheon"
        repo_slug = "ajoe734/pantheon"
        repo_root = ROOT
        target_branch = DEFAULT_DEV_BRANCH
        dedicated_integration_path = False

        try:
            repo_id = multi_repo_registry.validate_task_repository_scope(config_dict, raw)
            resolved_repo = multi_repo_registry.resolve_repository(config_dict, repo_id)
            repo_slug = str(resolved_repo.get("repo") or "").strip()
            if not repo_slug:
                scope_error = f"Repository `{repo_id}` has no configured GitHub slug"
            target_branch = (
                str(resolved_repo.get("default_branch") or DEFAULT_DEV_BRANCH).strip()
                or DEFAULT_DEV_BRANCH
            )
            integration_path_raw = str(
                resolved_repo.get("integration_path") or ""
            ).strip()
            dedicated_integration_path = bool(integration_path_raw)
            if integration_path_raw:
                configured_path = Path(integration_path_raw).expanduser()
                if not configured_path.is_absolute():
                    scope_error = (
                        f"Repository `{repo_id}` integration_path must be absolute"
                    )
                    configured_path = None
            else:
                configured_path = multi_repo_registry.repository_configured_local_path(
                    config_dict, repo_id
                )
            if configured_path is None:
                if scope_error is None:
                    scope_error = (
                        f"Repository `{repo_id}` has no configured integration_path "
                        "or local_path"
                    )
            else:
                repo_root = configured_path.resolve(strict=False)
        except (ValueError, RuntimeError) as exc:
            scope_error = str(exc)

        candidates.append(
            (
                0 if is_review_approved else 1,
                TaskCandidate(
                    task_id=task_id,
                    title=str(raw.get("title") or task_id).strip(),
                    owner=owner,
                    reviewer=reviewer,
                    branch=f"{task_branch_prefix}{task_id}",
                    repository_id=repo_id,
                    repository_slug=repo_slug,
                    repository_root=repo_root,
                    target_branch=target_branch,
                    dedicated_integration_path=dedicated_integration_path,
                    scope_error=scope_error,
                    raw_task=dict(raw),
                ),
            )
        )
    # Exact-head approvals are already waiting to land, so they cannot be
    # starved by an earlier active merge-then-review row whose PR is not open
    # yet. Python's stable sort preserves canonical row order within each lane.
    candidates.sort(key=lambda item: item[0])
    return [candidate for _, candidate in candidates]


def normalize_state(value: Any) -> str:
    return str(value or "").strip().upper().replace("-", "_")


def post_merge_task_handoff(candidate: TaskCandidate) -> str:
    status = str(candidate.raw_task.get("status") or "").strip().lower()
    if not status or status == "review_approved":
        operator_acceptance = candidate.raw_task.get("operator_acceptance")
        if isinstance(operator_acceptance, Mapping) and str(
            operator_acceptance.get("mode") or ""
        ).strip() == "operator_exact_head":
            return (
                f"left {candidate.task_id} in review_approved for Human/Ops exact-head "
                "closeout (no owner finalization)"
            )
        return (
            f"left {candidate.task_id} in review_approved for owner finalization"
        )
    return (
        f"left {candidate.task_id} at canonical status {status} "
        "for post-merge review/finalization"
    )


def is_active_merge_then_review(candidate: TaskCandidate) -> bool:
    status = str(candidate.raw_task.get("status") or "").strip().lower()
    contract = review_gate.contract_from_task_row(candidate.raw_task)
    return (
        status in {"in_progress", "review"}
        and contract.policy == review_gate.POLICY_MERGE_THEN_REVIEW
        and contract.declaration_honored
    )


def result_consumes_run_capacity(
    candidate: TaskCandidate, result: IntegrationResult
) -> bool:
    """Count actionable work, not observations that cannot mutate this pass."""

    del candidate
    return result.action in {"merged", "would_merge"}


def normalize_github_repo_slug(value: str | None) -> str:
    candidate = str(value or "").strip()
    if not candidate:
        return ""
    candidate = candidate.removesuffix(".git")
    if candidate.startswith("git@github.com:"):
        candidate = candidate[len("git@github.com:") :]
    elif candidate.startswith("ssh://git@github.com/"):
        candidate = candidate[len("ssh://git@github.com/") :]
    elif candidate.startswith("https://github.com/"):
        candidate = candidate[len("https://github.com/") :]
    elif candidate.startswith("http://github.com/"):
        candidate = candidate[len("http://github.com/") :]
    return candidate.strip("/")


def check_name(item: Mapping[str, Any]) -> str:
    for key in ("name", "context", "workflowName"):
        value = item.get(key)
        if value:
            return str(value)
    return "unnamed-check"


def is_check_required(item: Mapping[str, Any]) -> bool | None:
    """Classify data-driven requiredness of a check item.

    Returns:
      True if positively identified as required;
      False if positively identified as non-required (diagnostic);
      None if requiredness is missing or ambiguous (fails closed as required).
    """
    for key in ("isRequired", "is_required"):
        if key in item:
            value = item.get(key)
            if isinstance(value, bool):
                return value
    return None


def is_ignorable_diagnostic(item: Mapping[str, Any]) -> bool:
    """Return true only for an explicitly optional, known diagnostic issuer.

    GitHub's ``isRequired`` describes branch-protection requirements, not
    whether a check is merely diagnostic. Some substantive Branch CI jobs are
    intentionally optional, so requiredness alone must never downgrade their
    failures. The workflow provenance is therefore a second mandatory input.
    """

    if is_check_required(item) is not False:
        return False
    workflow_name = str(item.get("workflowName") or "").strip()
    return workflow_name in IGNORABLE_DIAGNOSTIC_WORKFLOWS


def summarize_status_rollup(rollup: Any) -> CheckSummary:
    if not isinstance(rollup, list) or not rollup:
        return CheckSummary("empty")
    failing: list[str] = []
    pending: list[str] = []
    ignored_diagnostic: list[str] = []
    for item in rollup:
        if not isinstance(item, Mapping):
            pending.append("malformed-check")
            continue
        is_non_required_diagnostic = is_ignorable_diagnostic(item)
        values = [
            normalize_state(item.get("conclusion")),
            normalize_state(item.get("state")),
            normalize_state(item.get("status")),
        ]
        values = [value for value in values if value]
        if any(value in FAILURE_VALUES for value in values):
            if is_non_required_diagnostic:
                ignored_diagnostic.append(check_name(item))
            else:
                failing.append(check_name(item))
            continue
        if any(value in PENDING_VALUES for value in values):
            if is_non_required_diagnostic:
                ignored_diagnostic.append(check_name(item))
            else:
                pending.append(check_name(item))
            continue
        if any(value in SUCCESS_VALUES for value in values):
            continue
        # GitHub CheckRun often reports status=COMPLETED with a SUCCESS
        # conclusion. If conclusion is absent, treat COMPLETED as pending-ish
        # rather than silently green.
        if is_non_required_diagnostic:
            ignored_diagnostic.append(check_name(item))
        else:
            pending.append(check_name(item))
    if failing:
        return CheckSummary("red", len(rollup), tuple(failing), tuple(pending), tuple(ignored_diagnostic))
    if pending:
        return CheckSummary("pending", len(rollup), (), tuple(pending), tuple(ignored_diagnostic))
    return CheckSummary("green", len(rollup), (), (), tuple(ignored_diagnostic))


def ignored_diagnostic_note(checks: CheckSummary) -> str:
    """Render auditable context for statuses excluded from merge blocking."""

    if not checks.ignored_diagnostic:
        return ""
    return (
        " Ignored explicitly non-required diagnostics: "
        f"{', '.join(checks.ignored_diagnostic)}."
    )


def canonical_review_gate_is_green(rollup: Any) -> bool:
    """Whether the workflow-owned canonical review check is green.

    This intentionally is not a substitute for ``summarize_status_rollup``:
    the latter still gates every check before a merge.  It only decides
    whether a pre-existing carry-forward proof needs the workflow to be
    dispatched again after a prior interrupted publication attempt.
    """

    if not isinstance(rollup, list):
        return False
    observed = False
    for item in rollup:
        if not isinstance(item, Mapping):
            continue
        if check_name(item) != github_review_bridge.CANONICAL_REVIEW_CONTEXT:
            continue
        values = [
            normalize_state(item.get("conclusion")),
            normalize_state(item.get("state")),
            normalize_state(item.get("status")),
        ]
        values = [value for value in values if value]
        if any(value in FAILURE_VALUES or value in PENDING_VALUES for value in values):
            return False
        if any(value in SUCCESS_VALUES for value in values):
            observed = True
    return observed


def pr_number(pr: Mapping[str, Any]) -> int | None:
    try:
        return int(pr.get("number"))
    except (TypeError, ValueError):
        return None


def gh_json(runner: CommandRunner, args: Sequence[str], *, cwd: Path = ROOT) -> Any:
    result = runner.run(["gh", *args], cwd=cwd)
    text = result.stdout.strip()
    if not text:
        return None
    return json.loads(text)


def fetch_is_required_map(
    runner: CommandRunner,
    url: str,
    number: int,
    *,
    root: Path = ROOT,
) -> dict[tuple[str, str], bool]:
    """Fetch isRequired for PR status checks via GraphQL.

    Returns a mapping of (typename, name_or_context) -> is_required boolean.
    Fails closed (returns empty dict) if GraphQL request fails.
    """
    repository = github_review_bridge.repository_from_pull_request_url(url)
    if not repository or "/" not in repository:
        return {}
    owner, repo = repository.split("/", 1)
    query = """
query($owner: String!, $repo: String!, $number: Int!) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      statusCheckRollup {
        contexts(first: 100) {
          nodes {
            __typename
            ... on CheckRun {
              name
              isRequired(pullRequestNumber: $number)
            }
            ... on StatusContext {
              context
              isRequired(pullRequestNumber: $number)
            }
          }
        }
      }
    }
  }
}
"""
    try:
        payload = gh_json(
            runner,
            [
                "api",
                "graphql",
                "-F",
                f"owner={owner}",
                "-F",
                f"repo={repo}",
                "-F",
                f"number={number}",
                "-f",
                f"query={query}",
            ],
            cwd=root,
        )
        if not isinstance(payload, Mapping):
            return {}
        data = payload.get("data")
        if not isinstance(data, Mapping):
            return {}
        repo_obj = data.get("repository")
        if not isinstance(repo_obj, Mapping):
            return {}
        pr_obj = repo_obj.get("pullRequest")
        if not isinstance(pr_obj, Mapping):
            return {}
        rollup_obj = pr_obj.get("statusCheckRollup")
        if not isinstance(rollup_obj, Mapping):
            return {}
        contexts_obj = rollup_obj.get("contexts")
        if not isinstance(contexts_obj, Mapping):
            return {}
        nodes = contexts_obj.get("nodes")
        if not isinstance(nodes, list):
            return {}

        result: dict[tuple[str, str], bool] = {}
        for node in nodes:
            if not isinstance(node, Mapping):
                continue
            typename = str(node.get("__typename") or "").strip()
            name_key = str(node.get("name") or node.get("context") or "").strip()
            is_req = node.get("isRequired")
            if typename and name_key and isinstance(is_req, bool):
                key = (typename, name_key)
                result[key] = result.get(key, False) or is_req
        return result
    except subprocess.TimeoutExpired:
        # A final authority-window timeout must abort the merge, never be
        # downgraded to "requiredness unavailable".
        raise
    except Exception:
        return {}


def enrich_pr_status_rollup(
    pr: Mapping[str, Any] | None,
    runner: CommandRunner,
    *,
    root: Path = ROOT,
) -> Mapping[str, Any] | None:
    if not isinstance(pr, Mapping):
        return pr
    rollup = pr.get("statusCheckRollup")
    if not isinstance(rollup, list) or not rollup:
        return pr

    all_present = all(
        isinstance(item, Mapping) and ("isRequired" in item or "is_required" in item)
        for item in rollup
    )
    if all_present:
        return pr

    number = pr_number(pr)
    url = str(pr.get("url") or "")
    if not number or not url:
        return pr

    is_req_map = fetch_is_required_map(runner, url, number, root=root)
    if not is_req_map:
        return pr

    new_rollup: list[dict[str, Any]] = []
    for item in rollup:
        if not isinstance(item, Mapping):
            new_rollup.append(item)
            continue
        item_dict = dict(item)
        if "isRequired" not in item_dict and "is_required" not in item_dict:
            typename = str(item_dict.get("__typename") or "").strip()
            name_key = check_name(item_dict)
            key = (typename, name_key)
            if key in is_req_map:
                item_dict["isRequired"] = is_req_map[key]
        new_rollup.append(item_dict)

    return {**pr, "statusCheckRollup": new_rollup}


def fetch_pr_for_task(
    candidate: TaskCandidate,
    settings: Settings,
    runner: CommandRunner,
    *,
    root: Path = ROOT,
    state: str = "open",
) -> Mapping[str, Any] | None:
    listing = gh_json(
        runner,
        [
            "pr",
            "list",
            "--head",
            candidate.branch,
            "--base",
            candidate.target_branch,
            "--state",
            state,
            "--json",
            "number",
            "--limit",
            "10",
        ],
        cwd=root,
    )
    if not isinstance(listing, list) or not listing:
        return None
    if state == "open" and len(listing) > 1:
        # GitHub ambiguity is never resolved by picking the first row: a second
        # open PR for the same task branch can carry a different head than the
        # one the reviewer approved.
        raise AmbiguousPullRequests(candidate.branch, [item.get("number") for item in listing])
    number = listing[0].get("number")
    if number is None:
        return None
    details = gh_json(
        runner,
        [
            "pr",
            "view",
            str(number),
            "--json",
            PR_DETAIL_FIELDS,
        ],
        cwd=root,
    )
    if not isinstance(details, Mapping):
        return None
    return enrich_pr_status_rollup(details, runner, root=root)


def validate_pr(candidate: TaskCandidate, pr: Mapping[str, Any], settings: Settings) -> str | None:
    if bool(pr.get("isDraft")):
        return "pr_is_draft"
    if str(pr.get("headRefName") or "") != candidate.branch:
        return "head_branch_mismatch"
    if str(pr.get("baseRefName") or "") != candidate.target_branch:
        return "base_branch_mismatch"
    pr_repo = github_review_bridge.repository_from_pull_request_url(pr.get("url"))
    if pr_repo and candidate.repository_slug:
        if pr_repo.strip().casefold() != candidate.repository_slug.strip().casefold():
            return "repository_mismatch"
    return None


def pr_merge_commit_oid(pr: Mapping[str, Any]) -> str:
    merge_commit = pr.get("mergeCommit")
    if not isinstance(merge_commit, Mapping):
        return ""
    return str(merge_commit.get("oid") or "").strip()


def target_contains_commit(
    oid: str,
    target_branch: str,
    runner: CommandRunner,
    *,
    root: Path = ROOT,
) -> bool:
    runner.run(["git", "fetch", "origin", target_branch, "--quiet"], cwd=root)
    result = runner.run(
        ["git", "merge-base", "--is-ancestor", oid, f"origin/{target_branch}"],
        cwd=root,
        check=False,
    )
    return result.returncode == 0


def _directory_is_writable(path: Path) -> bool:
    """Prove a directory can create and remove a file, not merely mode-check it."""

    fd: int | None = None
    probe_path: str | None = None
    try:
        fd, probe_path = tempfile.mkstemp(prefix=".pantheon-integrator-write-", dir=path)
        return True
    except OSError:
        return False
    finally:
        if fd is not None:
            os.close(fd)
        if probe_path is not None:
            try:
                os.unlink(probe_path)
            except FileNotFoundError:
                pass


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _read_lock_metadata(handle: Any) -> dict[str, Any]:
    handle.seek(0)
    raw = handle.read().strip()
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {"state": "invalid", "raw": raw[:200]}
    return dict(payload) if isinstance(payload, Mapping) else {"state": "invalid"}


def _write_lock_metadata(handle: Any, payload: Mapping[str, Any]) -> None:
    handle.seek(0)
    handle.truncate()
    json.dump(dict(payload), handle, sort_keys=True)
    handle.write("\n")
    handle.flush()
    os.fsync(handle.fileno())


def _lock_owner_detail(metadata: Mapping[str, Any]) -> str:
    try:
        pid = int(metadata.get("pid") or 0)
    except (TypeError, ValueError):
        pid = 0
    if pid > 0:
        state = "alive" if _pid_is_alive(pid) else "not alive"
        return f"pid={pid} ({state}), owner={metadata.get('owner_id') or 'legacy'}"
    return "owner metadata unavailable"


def _lock_path_matches_handle(lock_path: Path, handle: Any) -> bool:
    """Return whether ``lock_path`` still names the inode held by ``handle``.

    The pre-flock integrator used an ``O_EXCL`` sentinel which it unlinked on
    exit.  During a rolling migration a new runner can open that legacy inode
    immediately before the legacy owner unlinks it.  A flock on the now
    unlinked inode does not exclude another runner which recreates the path, so
    every successful acquisition must prove that the pathname and descriptor
    still identify the same inode before it can become merge owner.
    """

    try:
        opened = os.fstat(handle.fileno())
        current = os.stat(lock_path)
    except OSError:
        return False
    return (opened.st_dev, opened.st_ino) == (current.st_dev, current.st_ino)


def _release_lock_handle(handle: Any) -> None:
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def _new_lock_owner_metadata() -> dict[str, Any]:
    return {
        "schema": LOCK_SCHEMA,
        "state": "held",
        "owner": "supervisor_integration_runner",
        "owner_id": f"{os.uname().nodename}:{os.getpid()}:{time.time_ns()}",
        "pid": os.getpid(),
        "created_at": int(time.time()),
    }


def _publish_new_lock(
    lock_path: Path,
) -> tuple[Any, dict[str, Any]] | None:
    """Publish a fully initialized, already-flocked inode without replacement.

    A creator must never expose the empty inode produced by ``O_CREAT``.  The
    private inode is locked and durably initialized first, then a hard link is
    used as the no-replace publication primitive.  Losing the link race simply
    means reopening the winning stable pathname on the next loop iteration.
    """

    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{lock_path.name}.publish-", dir=lock_path.parent
    )
    temporary_path = Path(temporary_name)
    handle = os.fdopen(fd, "r+", encoding="utf-8")
    published = False
    try:
        os.chmod(temporary_path, 0o600)
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        metadata = _new_lock_owner_metadata()
        _write_lock_metadata(handle, metadata)
        try:
            os.link(temporary_path, lock_path)
        except FileExistsError:
            return None
        published = True
        if not _lock_path_matches_handle(lock_path, handle):
            raise IntegrationLockError(
                f"published auto-integrator lock inode changed: {lock_path}"
            )
        try:
            parent_fd = os.open(lock_path.parent, os.O_RDONLY)
            try:
                os.fsync(parent_fd)
            finally:
                os.close(parent_fd)
        except OSError:
            # Metadata is already fsynced and the link operation is atomic.
            # Some filesystems do not permit fsync on directories.
            pass
        return handle, metadata
    except OSError as exc:
        raise IntegrationLockError(
            f"cannot publish auto-integrator lock {lock_path}: {exc}"
        ) from exc
    finally:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass
        if not published or not _lock_path_matches_handle(lock_path, handle):
            if not handle.closed:
                _release_lock_handle(handle)


@contextmanager
def lock_file(lock_path: Path, *, enabled: bool = True) -> Iterator[None]:
    """Hold the integration lock with kernel lifetime and durable owner metadata.

    ``flock`` releases automatically if the runner disappears. The on-disk
    metadata makes contention diagnosable and lets a later runner record that
    it recovered a legacy/dead-owner sentinel without ever stealing a live
    kernel lock.
    """

    if not enabled:
        yield
        return
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise IntegrationLockError(
            f"cannot create auto-integrator lock parent {lock_path.parent}: {exc}"
        ) from exc
    if not lock_path.parent.is_dir() or not _directory_is_writable(lock_path.parent):
        raise IntegrationLockError(
            f"auto-integrator lock parent is not writable: {lock_path.parent}"
        )
    handle: Any | None = None
    owner_metadata: dict[str, Any] = {}
    while handle is None:
        created = _publish_new_lock(lock_path)
        if created is not None:
            handle, owner_metadata = created
            break
        try:
            flags = os.O_RDWR
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            fd = os.open(str(lock_path), flags)
        except OSError as exc:
            raise IntegrationLockError(
                f"cannot open auto-integrator lock {lock_path}: {exc}"
            ) from exc

        candidate_handle = os.fdopen(fd, "r+", encoding="utf-8")
        try:
            try:
                fcntl.flock(
                    candidate_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB
                )
            except OSError as exc:
                if exc.errno not in {errno.EACCES, errno.EAGAIN}:
                    raise IntegrationLockError(
                        f"cannot acquire auto-integrator lock {lock_path}: {exc}"
                    ) from exc
                raise IntegrationLockHeld(
                    f"auto-integrator lock is already held: {lock_path} "
                    "(owner metadata is protected by the active flock)"
                ) from exc

            if not _lock_path_matches_handle(lock_path, candidate_handle):
                # A legacy owner unlinked the sentinel after this descriptor was
                # opened. Never become merge owner on an unreachable inode.
                _release_lock_handle(candidate_handle)
                continue

            previous = _read_lock_metadata(candidate_handle)
            if not previous:
                raise IntegrationLockError(
                    f"auto-integrator lock metadata is empty: {lock_path}"
                )
            try:
                previous_pid = int(previous.get("pid") or 0)
            except (TypeError, ValueError):
                previous_pid = 0
            previous_state = str(previous.get("state") or "held").strip().lower()
            if previous and previous_state == "invalid":
                raise IntegrationLockError(
                    f"auto-integrator lock metadata is corrupt: {lock_path}"
                )
            if (
                previous
                and previous_state != "released"
                and _pid_is_alive(previous_pid)
            ):
                # Compatibility with the legacy O_EXCL sentinel: an old runner
                # can still be active without holding flock. Refuse to steal it.
                raise IntegrationLockHeld(
                    f"auto-integrator legacy lock has a live owner: {lock_path} "
                    f"({_lock_owner_detail(previous)})"
                )
            if previous and previous_state != "released" and previous_pid <= 0:
                raise IntegrationLockError(
                    f"auto-integrator lock metadata has no recoverable owner PID: {lock_path}"
                )

            # A legacy owner removes its sentinel before exiting. Recheck after
            # the PID observation so that its unlink cannot strand our flock on
            # the old inode while another runner locks a recreated path.
            if not _lock_path_matches_handle(lock_path, candidate_handle):
                _release_lock_handle(candidate_handle)
                continue

            owner_metadata = _new_lock_owner_metadata()
            if previous and previous_state != "released":
                owner_metadata["recovered_from"] = previous
            try:
                _write_lock_metadata(candidate_handle, owner_metadata)
            except OSError as exc:
                raise IntegrationLockError(
                    f"cannot write auto-integrator lock metadata {lock_path}: {exc}"
                ) from exc
            if not _lock_path_matches_handle(lock_path, candidate_handle):
                _release_lock_handle(candidate_handle)
                owner_metadata = {}
                continue
            handle = candidate_handle
        except Exception:
            if not candidate_handle.closed:
                _release_lock_handle(candidate_handle)
            raise

    try:
        yield
    finally:
        if owner_metadata:
            released = {
                **owner_metadata,
                "state": "released",
                "released_at": int(time.time()),
            }
            try:
                _write_lock_metadata(handle, released)
            except OSError:
                # Kernel unlock still happens; a later runner can recover the
                # stale held metadata after this process exits.
                pass
        _release_lock_handle(handle)


def fetch_refs(candidate: TaskCandidate, runner: CommandRunner, *, root: Path) -> None:
    runner.run(["git", "fetch", "origin", candidate.target_branch, "--quiet"], cwd=root)
    runner.run(
        [
            "git",
            "fetch",
            "origin",
            f"+refs/heads/{candidate.branch}:refs/remotes/origin/{candidate.branch}",
            "--quiet",
        ],
        cwd=root,
    )


def run_rebase_smoke(
    candidate: TaskCandidate,
    settings: Settings,
    runner: CommandRunner,
    *,
    root: Path,
    execute: bool,
    extra_smoke_commands: Sequence[str] = (),
    allow_push: bool = True,
    exact_head: str = "",
) -> tuple[bool, str]:
    fetch_refs(candidate, runner, root=root)
    commands = tuple(extra_smoke_commands) or settings.smoke_commands

    if not allow_push:
        # Review-before-merge PRs are approved by exact head.  Pantheon task
        # branches are composed by merging the current dev base into the task
        # branch, so running `git rebase origin/dev` here would linearize a
        # merge-rich, already-current graph and can report a false conflict.
        # For gated delivery the only safe question is whether the reviewed
        # head already contains the target base.  If it does, smoke that
        # immutable head directly; if it does not, require the owner to refresh
        # and get a new exact-head review.
        if not exact_head:
            return False, "exact_head_missing"
        ancestry = runner.run(
            [
                "git",
                "merge-base",
                "--is-ancestor",
                f"origin/{candidate.target_branch}",
                exact_head,
            ],
            cwd=root,
            check=False,
        )
        if ancestry.returncode == 0:
            with tempfile.TemporaryDirectory(prefix=f"pantheon-integrate-{candidate.task_id}-") as tmp:
                worktree = Path(tmp)
                runner.run(["git", "worktree", "add", "--detach", str(worktree), exact_head], cwd=root)
                try:
                    for command in commands:
                        runner.run_shell(command, cwd=worktree)
                    return False, "clean_exact_head"
                finally:
                    runner.run(["git", "worktree", "remove", "--force", str(worktree)], cwd=root, check=False)

        # The exact head is behind dev. Exercise the combined tree only to
        # distinguish a true conflict from ordinary staleness. The result is
        # never pushed or queued; the owner must refresh the task branch before
        # a later pass can issue a synchronous exact-head merge.
        with tempfile.TemporaryDirectory(prefix=f"pantheon-integrate-{candidate.task_id}-") as tmp:
            worktree = Path(tmp)
            runner.run(["git", "worktree", "add", "--detach", str(worktree), exact_head], cwd=root)
            try:
                merge = runner.run(
                    [
                        "git",
                        *DISPOSABLE_MERGE_IDENTITY,
                        "merge",
                        "--no-edit",
                        f"origin/{candidate.target_branch}",
                    ],
                    cwd=worktree,
                    check=False,
                )
                if merge.returncode != 0:
                    runner.run(["git", "merge", "--abort"], cwd=worktree, check=False)
                    return False, "exact_head_merge_conflict"
                for command in commands:
                    runner.run_shell(command, cwd=worktree)
                return False, "exact_head_verified_clean"
            finally:
                runner.run(["git", "worktree", "remove", "--force", str(worktree)], cwd=root, check=False)

    with tempfile.TemporaryDirectory(prefix=f"pantheon-integrate-{candidate.task_id}-") as tmp:
        worktree = Path(tmp)
        runner.run(["git", "worktree", "add", "--detach", str(worktree), f"origin/{candidate.branch}"], cwd=root)
        try:
            before = runner.run(["git", "rev-parse", "HEAD"], cwd=worktree).stdout.strip()
            rebase = runner.run(["git", "rebase", f"origin/{candidate.target_branch}"], cwd=worktree, check=False)
            if rebase.returncode != 0:
                runner.run(["git", "rebase", "--abort"], cwd=worktree, check=False)
                return False, "rebase_conflict"
            for command in commands:
                runner.run_shell(command, cwd=worktree)
            after = runner.run(["git", "rev-parse", "HEAD"], cwd=worktree).stdout.strip()
            changed = after != before
            pushed = False
            if execute and allow_push and changed:
                runner.run(
                    ["git", "push", "--force-with-lease", "origin", f"HEAD:{candidate.branch}"],
                    cwd=worktree,
                )
                pushed = True
            if pushed:
                return True, "rebased_and_pushed"
            # `rebase_required` means the branch would have to move to land.
            # Under review-before-merge that invalidates the reviewed head, so
            # the caller must not push it.
            return False, "rebase_required" if changed else "clean_rebase"
        finally:
            runner.run(["git", "worktree", "remove", "--force", str(worktree)], cwd=root, check=False)


def merge_command(
    candidate: TaskCandidate,
    number: int,
    *,
    exact_head: str,
) -> list[str]:
    slug = normalize_github_repo_slug(candidate.repository_slug)
    if len(slug.split("/")) != 2 or any(
        not part or part in {".", ".."} for part in slug.split("/")
    ):
        raise ValueError("direct task merge requires a canonical owner/repository slug")
    exact_head = str(exact_head or "").strip().lower()
    if not review_gate.OID_RE.fullmatch(exact_head):
        raise ValueError("direct task merge requires an exact 40-hex head commit")
    # The REST endpoint is synchronous and the `sha` field is an optimistic
    # concurrency guard. Unlike `gh pr merge`, it cannot silently hand merge
    # authority to auto-merge or a repository merge queue.
    return [
        "gh",
        "api",
        "--method",
        "PUT",
        f"repos/{slug}/pulls/{number}/merge",
        "-f",
        f"sha={exact_head}",
        "-f",
        f"merge_method={DEFAULT_MERGE_METHOD}",
    ]


def disable_auto_merge(
    number: int | None,
    runner: CommandRunner,
    *,
    root: Path,
    execute: bool,
) -> bool:
    """Revoke a premature auto-merge request on a gated PR."""

    if number is None or not execute:
        return False
    result = runner.run(["gh", "pr", "merge", str(number), "--disable-auto"], cwd=root, check=False)
    return result.returncode == 0


def read_auto_merge_request(
    number: int | None,
    runner: CommandRunner,
    *,
    root: Path,
) -> Any:
    """Read the live auto-merge grant after a revocation attempt.

    `gh pr merge --disable-auto` returning zero is not an authority signal by
    itself: the request can remain armed if GitHub/gh reports success while the
    server-side state was unchanged. The integrator must therefore re-read the
    PR before it emits any direct merge command.
    """

    if number is None:
        raise AutoIntegratorError("cannot verify autoMergeRequest without a PR number")
    try:
        payload = gh_json(
            runner,
            [
                "pr",
                "view",
                str(number),
                "--json",
                "autoMergeRequest",
            ],
            cwd=root,
        )
    except (CommandFailure, AutoIntegratorError, json.JSONDecodeError) as exc:
        raise AutoIntegratorError(f"cannot verify autoMergeRequest after revocation: {exc}") from exc
    if not isinstance(payload, Mapping) or "autoMergeRequest" not in payload:
        raise AutoIntegratorError("cannot verify autoMergeRequest after revocation: malformed gh response")
    return payload.get("autoMergeRequest")


def has_auto_merge_request(pr: Mapping[str, Any]) -> bool:
    return pr.get("autoMergeRequest") is not None


def _decision_status_is_eligible(decision: review_gate.GateDecision) -> bool:
    status = str(decision.contract.get("status") or "").strip().lower()
    if decision.policy == review_gate.POLICY_MERGE_THEN_REVIEW:
        return status in {"in_progress", "review"}
    return status == "review_approved"


def revalidate_before_merge(
    candidate: TaskCandidate,
    settings: Settings,
    runner: CommandRunner,
    *,
    root: Path,
    status_root: Path,
    canonical_state_file: Path | None,
    prior_gate: ReviewGate,
    prior_decision: review_gate.GateDecision,
    prior_pr_number: int | None,
) -> tuple[Mapping[str, Any], review_gate.GateDecision, CheckSummary]:
    """Re-read canonical authority and the exact live PR immediately before merge."""

    if canonical_state_file is None:
        fresh_state = prior_gate.state
    else:
        try:
            fresh_state = load_json(canonical_state_file, {})
        except (OSError, json.JSONDecodeError) as exc:
            raise FinalMergeRevalidationError(
                "canonical-state-refresh-failed",
                f"Cannot refresh canonical task state before merge: {exc}",
            ) from exc
    if not isinstance(fresh_state, Mapping):
        raise FinalMergeRevalidationError(
            "canonical-state-refresh-failed",
            "Canonical task state is not a JSON object at final merge revalidation.",
        )

    fresh_gate = ReviewGate(
        status_root=status_root,
        state=fresh_state,
        # Unit tests inject immutable audit fixtures. Production leaves this as
        # None, causing the gate to re-read the canonical audit on every call.
        events=prior_gate.events,
    )
    try:
        fresh_pr = fetch_pr_for_task(candidate, settings, runner, root=root)
    except (AmbiguousPullRequests, CommandFailure, AutoIntegratorError, OSError) as exc:
        raise FinalMergeRevalidationError(
            "final-pr-refresh-failed",
            f"Cannot refresh the exact task PR before merge: {exc}",
        ) from exc
    if fresh_pr is None:
        raise FinalMergeRevalidationError(
            "final-pr-missing",
            "The task PR is no longer open at final merge revalidation.",
        )
    fresh_number = pr_number(fresh_pr)
    if fresh_number != prior_pr_number:
        raise FinalMergeRevalidationError(
            "final-pr-changed",
            f"Task PR changed from #{prior_pr_number} to #{fresh_number} during integration.",
        )
    problem = validate_pr(candidate, fresh_pr, settings)
    if problem:
        raise FinalMergeRevalidationError(
            f"final-{problem.replace('_', '-')}",
            f"PR #{fresh_number} failed final validation: {problem}.",
        )

    fresh_carry_forward = fresh_gate.task_brief_carry_forward(
        candidate, fresh_pr, runner, root=root
    )
    fresh_decision = fresh_gate.decide(
        candidate,
        fresh_pr,
        settings,
        task_brief_carry_forward=fresh_carry_forward,
    )
    if not fresh_decision.allow_merge or not _decision_status_is_eligible(
        fresh_decision
    ):
        raise FinalMergeRevalidationError(
            "final-review-gate-changed",
            f"Canonical merge authority changed during integration: "
            f"{fresh_decision.reason} - {fresh_decision.detail}.",
        )
    for field_name in ("policy", "owner", "reviewer"):
        prior_value = (
            prior_decision.policy
            if field_name == "policy"
            else str(prior_decision.contract.get(field_name) or "")
        )
        fresh_value = (
            fresh_decision.policy
            if field_name == "policy"
            else str(fresh_decision.contract.get(field_name) or "")
        )
        if fresh_value != prior_value:
            raise FinalMergeRevalidationError(
                "final-review-contract-changed",
                f"Canonical task {field_name} changed during integration "
                f"({prior_value!r} -> {fresh_value!r}).",
            )

    prior_head = str(prior_decision.head_oid or "").strip().lower()
    fresh_head = str(fresh_decision.head_oid or "").strip().lower()
    if not review_gate.OID_RE.fullmatch(prior_head) or fresh_head != prior_head:
        raise FinalMergeRevalidationError(
            "final-head-changed",
            f"PR #{fresh_number} head changed during gate/check/smoke validation "
            f"({prior_head or 'missing'} -> {fresh_head or 'missing'}).",
        )

    if has_auto_merge_request(fresh_pr):
        # Revocation, when needed, happens before smoke. Never mutate GitHub
        # authority while holding the canonical state/audit read locks. A new
        # request appearing in the final window is an authority change and the
        # safe outcome is to block after releasing those locks.
        raise FinalMergeRevalidationError(
            "final-auto-merge-armed",
            f"PR #{fresh_number} has an auto-merge request at final revalidation.",
        )

    fresh_checks = summarize_status_rollup(fresh_pr.get("statusCheckRollup"))
    if fresh_checks.state == "red":
        raise FinalMergeRevalidationError(
            "final-ci-red",
            f"PR #{fresh_number} checks changed to failing: "
            f"{', '.join(fresh_checks.failing)}.",
        )
    if fresh_checks.state in {"pending", "empty"}:
        raise FinalMergeRevalidationError(
            "final-ci-not-green",
            f"PR #{fresh_number} checks changed to {fresh_checks.state}.",
            waiting=True,
        )
    fresh_merge_state = normalize_state(fresh_pr.get("mergeStateStatus"))
    if fresh_merge_state and fresh_merge_state not in ALLOWED_DIRECT_MERGE_STATES:
        raise FinalMergeRevalidationError(
            "final-merge-state-not-direct",
            f"PR #{fresh_number} final mergeStateStatus={fresh_merge_state}; "
            "refusing an auto-merge or merge-queue handoff.",
            waiting=True,
        )
    return fresh_pr, fresh_decision, fresh_checks


@contextmanager
def final_authority_read_locks(
    *,
    execute: bool,
    canonical_state_file: Path | None,
    status_root: Path,
) -> Iterator[None]:
    """Freeze task-state then approval-audit authority for final merge."""

    if not execute:
        yield
        return
    state_file = canonical_state_file or status_root / "ai-status.json"
    activity_file = status_root / review_gate.ACTIVITY_LOG_NAME
    try:
        with orchestrator_common.canonical_task_state_lock_file(
            state_file, shared=True
        ):
            with orchestrator_common.activity_audit_lock_file(
                activity_file, shared=True
            ):
                yield
    except FinalMergeRevalidationError:
        raise
    except (OSError, RuntimeError, ValueError) as exc:
        raise FinalMergeRevalidationError(
            "canonical-authority-lock-failed",
            f"Cannot freeze canonical task/review authority before merge: {exc}",
        ) from exc


def unblock_task_id(task_id: str, reason: str) -> str:
    safe_reason = "".join(ch if ch.isalnum() else "-" for ch in reason.upper()).strip("-")
    return f"INTEGRATION-UNBLOCK-{task_id}-{safe_reason}"[:96]


def _write_unblock_request(root: Path, payload: Mapping[str, Any]) -> None:
    """Durably publish one immutable, content-addressed supervisor request."""

    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    request_id = hashlib.sha256(encoded).hexdigest()
    inbox = root / UNBLOCK_REQUEST_INBOX
    inbox.mkdir(mode=0o700, parents=True, exist_ok=True)
    destination = inbox / f"{request_id}.json"
    if destination.exists():
        if destination.read_bytes() != encoded + b"\n":
            raise AutoIntegratorError("content-addressed unblock request collision")
        return
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{request_id}.", suffix=".tmp", dir=inbox
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, destination)
        directory_fd = os.open(inbox, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary.exists():
            temporary.unlink()


def open_unblock_task(
    candidate: TaskCandidate,
    reason: str,
    detail: str,
    settings: Settings,
    runner: CommandRunner,
    *,
    root: Path,
    execute: bool,
) -> str:
    task_id = unblock_task_id(candidate.task_id, reason)
    if not execute:
        return task_id
    owner = settings.unblock_owner or candidate.owner
    reviewer = settings.unblock_reviewer or candidate.reviewer
    runtime_sha = str(os.environ.get("PANTHEON_COMMAND_RUNTIME_SHA") or "").lower()
    if not review_gate.OID_RE.fullmatch(runtime_sha):
        raise AutoIntegratorError(
            "PANTHEON_COMMAND_RUNTIME_SHA must bind unblock requests to an immutable runtime"
        )
    binding = candidate.raw_task.get("delivery_binding")
    binding = binding if isinstance(binding, Mapping) else {}
    pr_number = binding.get("pr") or binding.get("pr_number")
    head_sha = str(binding.get("head_sha") or "").lower()
    if not isinstance(pr_number, int) or pr_number < 1 or not review_gate.OID_RE.fullmatch(head_sha):
        raise AutoIntegratorError(
            f"canonical delivery binding for {candidate.task_id} lacks exact PR/head"
        )
    _write_unblock_request(
        root,
        {
            "schema": UNBLOCK_REQUEST_SCHEMA,
            "status_root": str(root.resolve()),
            "command_runtime_sha": runtime_sha,
            "source_task_id": candidate.task_id,
            "source_task_generation": int(candidate.raw_task.get("generation") or 1),
            "unblock_task_id": task_id,
            "reason": reason,
            "detail": detail[:500],
            "repository_id": candidate.repository_id,
            "repository_slug": candidate.repository_slug,
            "pr": pr_number,
            "head_sha": head_sha,
            "owner": owner,
            "reviewer": reviewer,
        },
    )
    return task_id


def preflight_repository(
    candidate: TaskCandidate,
    runner: CommandRunner,
    target_root: Path,
    *,
    require_standalone_integration: bool = False,
) -> tuple[str, str] | None:
    if not target_root.is_absolute():
        return (
            "invalid-repository-root",
            f"Cannot integrate {candidate.task_id}: repository root for {candidate.repository_id} must be an absolute path ({target_root}).",
        )
    check_fs = getattr(runner, "check_filesystem_paths", True)
    if check_fs and not target_root.is_dir():
        return (
            "missing-repository-checkout",
            f"Cannot integrate {candidate.task_id}: registered repository root for {candidate.repository_id} does not exist: {target_root}.",
        )
    top_proc = runner.run(["git", "rev-parse", "--show-toplevel"], cwd=target_root, check=False)
    if top_proc.returncode != 0:
        return (
            "invalid-git-repository",
            f"Cannot integrate {candidate.task_id}: repository root for {candidate.repository_id} is not a git repository ({target_root}).",
        )
    top_output = top_proc.stdout.strip()
    if check_fs and top_output:
        try:
            top_path = Path(top_output).resolve(strict=False)
            if top_path != target_root.resolve(strict=False):
                return (
                    "invalid-git-repository",
                    f"Cannot integrate {candidate.task_id}: repository root for {candidate.repository_id} is not a git toplevel ({top_path} != {target_root}).",
                )
        except OSError:
            pass
    if require_standalone_integration:
        head_proc = runner.run(
            ["git", "rev-parse", "HEAD"], cwd=target_root, check=False
        )
        head = head_proc.stdout.strip().lower()
        if (
            head_proc.returncode != 0
            or not review_gate.OID_RE.fullmatch(head)
            or target_root.name.lower() != head
        ):
            return (
                "integration-checkout-identity-mismatch",
                f"Cannot integrate {candidate.task_id}: dedicated integration root must be named for its exact HEAD ({target_root.name} != {head or 'missing'}).",
            )
        symbolic_proc = runner.run(
            ["git", "symbolic-ref", "-q", "HEAD"],
            cwd=target_root,
            check=False,
        )
        if symbolic_proc.returncode == 0:
            return (
                "integration-checkout-not-detached",
                f"Cannot integrate {candidate.task_id}: dedicated integration root must have detached HEAD: {target_root}.",
            )
    if check_fs and not _directory_is_writable(target_root):
        return (
            "repository-checkout-not-writable",
            f"Cannot integrate {candidate.task_id}: repository checkout is not writable: {target_root}.",
        )
    common_proc = runner.run(
        ["git", "rev-parse", "--git-common-dir"], cwd=target_root, check=False
    )
    common_raw = common_proc.stdout.strip()
    if common_proc.returncode != 0 or not common_raw:
        return (
            "invalid-git-common-dir",
            f"Cannot integrate {candidate.task_id}: git common dir is unavailable for {target_root}.",
        )
    common_dir = Path(common_raw)
    if not common_dir.is_absolute():
        common_dir = target_root / common_dir
    common_dir = common_dir.resolve(strict=False)
    if require_standalone_integration:
        expected_common = (target_root / ".git").resolve(strict=False)
        if common_dir != expected_common or (check_fs and not expected_common.is_dir()):
            return (
                "integration-checkout-not-standalone",
                f"Cannot integrate {candidate.task_id}: dedicated integration root must own its standalone .git directory ({common_dir} != {expected_common}).",
            )
    if check_fs and (
        not common_dir.is_dir() or not _directory_is_writable(common_dir)
    ):
        return (
            "git-common-dir-not-writable",
            f"Cannot integrate {candidate.task_id}: git common dir is not writable: {common_dir}.",
        )
    status_proc = runner.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=target_root,
        check=False,
    )
    if status_proc.returncode != 0:
        return (
            "repository-status-unavailable",
            f"Cannot integrate {candidate.task_id}: repository status is unavailable for {target_root}.",
        )
    dirty = status_proc.stdout.strip()
    if dirty:
        first_entry = dirty.splitlines()[0][:200]
        return (
            "dirty-repository-checkout",
            f"Cannot integrate {candidate.task_id}: repository checkout is not clean ({first_entry}).",
        )
    expected_slug = normalize_github_repo_slug(candidate.repository_slug)
    if not expected_slug:
        return (
            "missing-repository-slug",
            f"Cannot integrate {candidate.task_id}: repository `{candidate.repository_id}` has no configured GitHub slug.",
        )
    remote_proc = runner.run(["git", "remote", "get-url", "origin"], cwd=target_root, check=False)
    if remote_proc.returncode != 0:
        return (
            "missing-origin-remote",
            f"Cannot integrate {candidate.task_id}: origin remote is unavailable for {candidate.repository_id} at {target_root}.",
        )
    remote_raw = remote_proc.stdout.strip()
    actual_slug = normalize_github_repo_slug(remote_raw)
    is_local_fixture = (
        remote_raw.startswith("/")
        or remote_raw.startswith("file://")
        or (Path(remote_raw).exists() if len(remote_raw) < 260 else False)
    )
    if not is_local_fixture and (not actual_slug or actual_slug != expected_slug):
        return (
            "repository-origin-mismatch",
            f"Cannot integrate {candidate.task_id}: repository origin remote mismatch for {candidate.repository_id} at {target_root} ({actual_slug or 'missing'} != {expected_slug}).",
        )
    return None


def _canonical_task_state_event_path(config: Mapping[str, Any] | None) -> Path | None:
    """The live V2 journal path, only when this process runs in authoritative
    store mode -- mirrors ``scripts/ai_status.py``'s own ``store_mode`` check
    rather than assuming it (SD.md DTG-INT-01 §6.4 point 6).

    The auto-integrator is cron-launched, not supervisor-launched, so it does
    not inherit ``PANTHEON_TASK_STATE_STORE_MODE``/``PANTHEON_TASK_STATE_EVENT_LOG``
    the way a supervisor-spawned worker does; the live config's own
    ``task_state_store`` block is the authoritative source here, matching how
    ``resolve_execute_authority`` already reads ``paths``/``watchdog`` directly
    from the same config rather than assuming an inherited environment. The
    environment variables are checked first only so an explicit override (as
    used in tests) still wins.
    """

    mode = str(os.environ.get("PANTHEON_TASK_STATE_STORE_MODE") or "").strip().lower()
    raw = str(os.environ.get("PANTHEON_TASK_STATE_EVENT_LOG") or "").strip()
    if not mode and not raw and isinstance(config, Mapping):
        store_config = config.get("task_state_store")
        if isinstance(store_config, Mapping):
            mode = str(store_config.get("mode") or "").strip().lower()
            raw = str(store_config.get("event_log") or "").strip()
    if mode != "authoritative" or not raw:
        return None
    path = Path(os.path.expanduser(raw))
    return path if path.is_absolute() else None


def _record_merge_integration_receipt(
    candidate: TaskCandidate,
    *,
    observation: str,
    pr: int | None,
    head_sha: str,
    merge_commit_sha: str,
    status_root: Path,
    status_file: Path | None,
    config: Mapping[str, Any] | None,
    lock_path: Path,
) -> None:
    """Best-effort DTG-INT-01 receipt write after a real merge/reconciliation.

    Never raises: the merge itself already succeeded (or was already
    reconciled) by the time this runs, so a receipt failure must leave the
    task in ``review_approved`` for the next cron to reconcile again
    (SD.md §6.5), not fail the run that just landed real work.
    """

    if config is None or status_file is None or pr is None:
        return
    head_sha = str(head_sha or "").strip().lower()
    merge_commit_sha = str(merge_commit_sha or "").strip().lower()
    if not review_gate.OID_RE.fullmatch(head_sha) or not review_gate.OID_RE.fullmatch(merge_commit_sha):
        print(
            f"auto-integrator: integration_receipt skipped for {candidate.task_id}: "
            "missing exact head or merge commit oid",
            file=sys.stderr,
        )
        return
    raw_generation = candidate.raw_task.get("generation", 1)
    expected_generation = (
        raw_generation
        if isinstance(raw_generation, int) and not isinstance(raw_generation, bool)
        else 1
    )
    binding = integration_receipt.IntegrationBinding(
        repository=candidate.repository_slug,
        target_branch=candidate.target_branch,
        pr=pr,
        head_sha=head_sha,
    )
    authority = integration_receipt.IntegrationAuthority(
        command_root=ROOT,
        command_sha=ROOT.name,
        command_remote=orchestrator_common.status_command_expected_remote(dict(config)),
        command_base_ref=orchestrator_common.status_command_base_ref(dict(config)),
        status_root=status_root,
        lock_path=lock_path,
        lock_schema=LOCK_SCHEMA,
        lock_pid=os.getpid(),
    )
    try:
        integration_receipt.record_integration_receipt(
            config=config,
            task_id=candidate.task_id,
            expected_generation=expected_generation,
            expected_delivery_binding=binding,
            observation=observation,
            merge_commit_sha=merge_commit_sha,
            observed_at=orchestrator_common.utc_now(),
            status_file=status_file,
            event_path=_canonical_task_state_event_path(config),
            authority=authority,
        )
    except integration_receipt.IntegrationReceiptError as exc:
        print(
            f"auto-integrator: integration_receipt write failed for {candidate.task_id}: {exc}",
            file=sys.stderr,
        )


def integrate_candidate(
    candidate: TaskCandidate,
    settings: Settings,
    runner: CommandRunner,
    *,
    root: Path | None = None,
    status_root: Path | None = None,
    canonical_state_file: Path | None = None,
    execute: bool = False,
    require_dedicated_integration_path: bool = False,
    open_unblock: bool = True,
    extra_smoke_commands: Sequence[str] = (),
    gate: ReviewGate | None = None,
    config: Mapping[str, Any] | None = None,
) -> IntegrationResult:
    gate = gate or ReviewGate()
    status_root_dir = status_root if status_root is not None else gate.status_root
    target_root = root if root is not None else candidate.repository_root

    if require_dedicated_integration_path and not candidate.dedicated_integration_path:
        detail = (
            f"Cannot integrate {candidate.task_id}: repository `{candidate.repository_id}` "
            "has no explicit integration_path; live execution may not use its "
            "worker/source local_path as the merge checkout."
        )
        unblock = (
            open_unblock_task(
                candidate,
                "missing-dedicated-integration-path",
                detail,
                settings,
                runner,
                root=status_root_dir,
                execute=execute,
            )
            if open_unblock
            else None
        )
        return IntegrationResult(
            candidate.task_id,
            "blocked",
            detail,
            unblock_task_id=unblock,
            dry_run=not execute,
            commands=runner.commands[:],
        )

    if candidate.scope_error:
        detail = f"Cannot integrate {candidate.task_id}: {candidate.scope_error}."
        unblock = (
            open_unblock_task(
                candidate,
                "invalid-repository-scope",
                detail,
                settings,
                runner,
                root=status_root_dir,
                execute=execute,
            )
            if open_unblock
            else None
        )
        return IntegrationResult(
            candidate.task_id,
            "blocked",
            detail,
            unblock_task_id=unblock,
            dry_run=not execute,
            commands=runner.commands[:],
        )

    preflight_error = preflight_repository(
        candidate,
        runner,
        target_root,
        require_standalone_integration=require_dedicated_integration_path,
    )
    if preflight_error:
        reason, detail = preflight_error
        unblock = (
            open_unblock_task(
                candidate,
                reason,
                detail,
                settings,
                runner,
                root=status_root_dir,
                execute=execute,
            )
            if open_unblock
            else None
        )
        return IntegrationResult(
            candidate.task_id,
            "blocked",
            detail,
            unblock_task_id=unblock,
            dry_run=not execute,
            commands=runner.commands[:],
        )

    try:
        pr = fetch_pr_for_task(candidate, settings, runner, root=target_root)
    except AmbiguousPullRequests as exc:
        detail = f"{exc}; refusing to choose a head for {candidate.task_id}."
        unblock = (
            open_unblock_task(
                candidate,
                "ambiguous-open-prs",
                detail,
                settings,
                runner,
                root=status_root_dir,
                execute=execute,
            )
            if open_unblock
            else None
        )
        return IntegrationResult(
            candidate.task_id,
            "blocked",
            detail,
            unblock_task_id=unblock,
            dry_run=not execute,
            commands=runner.commands[:],
        )
    except (CommandFailure, AutoIntegratorError, OSError) as exc:
        detail = f"Failed to inspect PR for {candidate.task_id} at {target_root}: {exc}"
        unblock = (
            open_unblock_task(
                candidate,
                "pr-lookup-failed",
                detail,
                settings,
                runner,
                root=status_root_dir,
                execute=execute,
            )
            if open_unblock
            else None
        )
        return IntegrationResult(
            candidate.task_id,
            "blocked",
            detail,
            unblock_task_id=unblock,
            dry_run=not execute,
            commands=runner.commands[:],
        )
    if pr is None:
        merged_pr = fetch_pr_for_task(candidate, settings, runner, root=target_root, state="merged")
        if merged_pr is not None:
            number = pr_number(merged_pr)
            url = str(merged_pr.get("url") or "")
            problem = validate_pr(candidate, merged_pr, settings)
            if problem:
                detail = f"Merged PR #{number} is not eligible for reconciliation: {problem}."
                unblock = (
                    open_unblock_task(
                        candidate,
                        problem,
                        detail,
                        settings,
                        runner,
                        root=status_root_dir,
                        execute=execute,
                    )
                    if open_unblock
                    else None
                )
                return IntegrationResult(
                    candidate.task_id,
                    "blocked",
                    detail,
                    number,
                    url,
                    unblock,
                    not execute,
                    runner.commands[:],
                )
            oid = pr_merge_commit_oid(merged_pr)
            if not oid:
                detail = f"Merged PR #{number} has no merge commit oid; cannot reconcile {candidate.task_id}."
                unblock = (
                    open_unblock_task(
                        candidate,
                        "merged-pr-no-merge-commit",
                        detail,
                        settings,
                        runner,
                        root=status_root_dir,
                        execute=execute,
                    )
                    if open_unblock
                    else None
                )
                return IntegrationResult(
                    candidate.task_id,
                    "blocked",
                    detail,
                    number,
                    url,
                    unblock,
                    not execute,
                    runner.commands[:],
                )
            if not target_contains_commit(oid, candidate.target_branch, runner, root=target_root):
                detail = (
                    f"Merged PR #{number} merge commit {oid} is not in "
                    f"origin/{candidate.target_branch}; not reconciling."
                )
                return IntegrationResult(
                    candidate.task_id,
                    "waiting",
                    detail,
                    number,
                    url,
                    dry_run=not execute,
                    commands=runner.commands[:],
                )
            merged_carry_forward = gate.task_brief_carry_forward(
                candidate,
                merged_pr,
                runner,
                root=target_root,
            )
            merged_decision = gate.decide(
                candidate,
                merged_pr,
                settings,
                task_brief_carry_forward=merged_carry_forward,
            )
            if (
                merged_decision.policy == review_gate.POLICY_REVIEW_BEFORE_MERGE
                and not merged_decision.allow_merge
            ):
                # An already-merged PR that the gate would have refused must not
                # be laundered into `done` by the reconciliation path.
                detail = (
                    f"Merged PR #{number} does not satisfy review-before-merge for "
                    f"{candidate.task_id}: {merged_decision.reason} - {merged_decision.detail}."
                )
                unblock = (
                    open_unblock_task(
                        candidate,
                        f"review-gate-{merged_decision.reason.replace('_', '-')}",
                        detail,
                        settings,
                        runner,
                        root=status_root_dir,
                        execute=execute,
                    )
                    if open_unblock
                    else None
                )
                return IntegrationResult(
                    candidate.task_id,
                    "blocked",
                    detail,
                    number,
                    url,
                    unblock,
                    not execute,
                    runner.commands[:],
                )
            if not execute:
                detail = (
                    f"Dry-run: PR #{number} is already merged into {candidate.target_branch}; "
                    f"{post_merge_task_handoff(candidate)}."
                )
                return IntegrationResult(
                    candidate.task_id,
                    "already_merged",
                    detail,
                    number,
                    url,
                    dry_run=True,
                    commands=runner.commands[:],
                )
            try:
                gate.publish_task_brief_carry_forward(
                    candidate,
                    merged_pr,
                    runner,
                    root=target_root,
                    carried=merged_carry_forward,
                    decision=merged_decision,
                )
            except AutoIntegratorError as exc:
                detail = (
                    f"Merged PR #{number} has a gate-approved carry-forward but {exc}; "
                    "refusing integration."
                )
                return IntegrationResult(
                    candidate.task_id,
                    "blocked",
                    detail,
                    number,
                    url,
                    dry_run=False,
                    commands=runner.commands[:],
                )
            _record_merge_integration_receipt(
                candidate,
                observation=integration_receipt.RECEIPT_OBSERVATION_RECONCILED,
                pr=number,
                head_sha=merged_decision.head_oid,
                merge_commit_sha=oid,
                status_root=status_root_dir,
                status_file=canonical_state_file,
                config=config,
                lock_path=settings.lock_path,
            )
            detail = (
                f"PR #{number} is already merged into {candidate.target_branch}; "
                f"{post_merge_task_handoff(candidate)}."
            )
            return IntegrationResult(
                candidate.task_id,
                "already_merged",
                detail,
                number,
                url,
                dry_run=False,
                commands=runner.commands[:],
            )

        detail = f"No open or merged PR found for {candidate.branch} -> {candidate.target_branch}."
        if is_active_merge_then_review(candidate):
            return IntegrationResult(
                candidate.task_id,
                "not_ready",
                f"{detail} Active merge-then-review work has not submitted a PR yet.",
                dry_run=not execute,
                commands=runner.commands[:],
            )
        unblock = (
            open_unblock_task(
                candidate,
                "missing-pr",
                detail,
                settings,
                runner,
                root=status_root_dir,
                execute=execute,
            )
            if open_unblock
            else None
        )
        return IntegrationResult(
            candidate.task_id,
            "blocked",
            detail,
            unblock_task_id=unblock,
            dry_run=not execute,
            commands=runner.commands[:],
        )
    number = pr_number(pr)
    url = str(pr.get("url") or "")
    problem = validate_pr(candidate, pr, settings)
    if problem:
        detail = f"PR #{number} is not eligible: {problem}."
        unblock = (
            open_unblock_task(
                candidate,
                problem,
                detail,
                settings,
                runner,
                root=status_root_dir,
                execute=execute,
            )
            if open_unblock
            else None
        )
        return IntegrationResult(
            candidate.task_id,
            "blocked",
            detail,
            number,
            url,
            unblock,
            not execute,
            runner.commands[:],
        )

    # Canonical review-before-merge gate. This runs before the CI and merge
    # state probes so a premature auto-merge request is revoked immediately
    # rather than after the checks happen to turn green.
    carry_forward = gate.task_brief_carry_forward(
        candidate,
        pr,
        runner,
        root=target_root,
    )
    decision = gate.decide(
        candidate,
        pr,
        settings,
        task_brief_carry_forward=carry_forward,
    )
    gated = decision.policy == review_gate.POLICY_REVIEW_BEFORE_MERGE
    if not review_gate.OID_RE.fullmatch(str(decision.head_oid or "").strip()):
        detail = (
            f"PR #{number} gate decision does not bind a valid exact head; "
            "refusing gate/check/smoke validation on a branch ref."
        )
        return IntegrationResult(
            candidate.task_id,
            "blocked",
            detail,
            number,
            url,
            dry_run=not execute,
            commands=runner.commands[:],
        )
    # A gated PR must never hold an auto-merge request, whatever the gate went
    # on to decide and whatever GitHub currently thinks of its merge state. PR
    # #4201 sat BEHIND with auto-merge armed and no approval: only the stale
    # base was holding it back, and it would have merged the moment the base
    # caught up. Revoke first, then classify.
    revocation_command_succeeded = False
    revocation_read_error = ""
    revocation_attempted = has_auto_merge_request(pr)
    if revocation_attempted:
        revocation_command_succeeded = disable_auto_merge(
            number, runner, root=target_root, execute=execute
        )
        if execute:
            try:
                live_auto_merge_request = read_auto_merge_request(
                    number, runner, root=target_root
                )
            except AutoIntegratorError as exc:
                revocation_read_error = str(exc)
            else:
                pr = {**pr, "autoMergeRequest": live_auto_merge_request}
    if gated and not decision.allow_merge:
        detail = (
            f"PR #{number} is gated by review-before-merge and not mergeable: "
            f"{decision.reason} - {decision.detail}."
        )
        if revocation_read_error:
            detail += f" {revocation_read_error}."
        elif has_auto_merge_request(pr):
            detail += (
                " Revoked the pending auto-merge request."
                if revocation_command_succeeded and not execute
                else " A pending auto-merge request is still set on this PR."
            )
        unblock = (
            open_unblock_task(
                candidate,
                f"review-gate-{decision.reason.replace('_', '-')}",
                detail,
                settings,
                runner,
                root=status_root_dir,
                execute=execute,
            )
            if open_unblock
            else None
        )
        return IntegrationResult(
            candidate.task_id,
            "blocked",
            detail,
            number,
            url,
            unblock,
            not execute,
            runner.commands[:],
        )

    if revocation_attempted and execute and (
        revocation_read_error or has_auto_merge_request(pr)
    ):
        # The gate approved this head, but the post-revocation readback is
        # unavailable or still shows the merge grant armed. The command's exit
        # status is diagnostic only: a zero can leave the grant armed, while a
        # nonzero can race with another actor that already turned it off.
        # Proceeding would emit a synchronous REST merge while GitHub may
        # independently hold authority to land whatever head stands next.
        # Stop before any merge call is emitted.
        if revocation_read_error:
            reason = revocation_read_error
        elif not revocation_command_succeeded:
            reason = (
                "`gh pr merge --disable-auto` failed and the post-revocation "
                "readback still shows autoMergeRequest armed"
            )
        else:
            reason = "post-revocation readback still shows autoMergeRequest armed"
        detail = (
            f"PR #{number} is approved for head {decision.head_oid} but {reason}, "
            "so a pending auto-merge request may still be armed on it. Refusing "
            "to merge with a standing merge grant; revoke it manually and re-run "
            "the integrator."
        )
        unblock = (
            open_unblock_task(
                candidate,
                "auto-merge-revocation-failed",
                detail,
                settings,
                runner,
                root=status_root_dir,
                execute=execute,
            )
            if open_unblock
            else None
        )
        return IntegrationResult(
            candidate.task_id,
            "blocked",
            detail,
            number,
            url,
            unblock,
            not execute,
            runner.commands[:],
        )

    # A direct generated-task-brief successor is allowed by the canonical
    # review gate, but GitHub's workflow-owned required context belongs to the
    # successor SHA.  Publish its tag/ref and dispatch that workflow *before*
    # examining the whole CI rollup: the old context is expected to be red
    # until this dispatch runs.  This pass never merges; the later pass that
    # observes the refreshed green context can continue through the ordinary
    # rollup and exact-head merge checks below.
    if (
        execute
        and decision.reason == "task_brief_only_approval_carried_forward"
        and isinstance(carry_forward, Mapping)
    ):
        try:
            publication = gate.publish_task_brief_carry_forward(
                candidate,
                pr,
                runner,
                root=target_root,
                carried=carry_forward,
                decision=decision,
                dispatch_if_proof_exists=not canonical_review_gate_is_green(
                    pr.get("statusCheckRollup")
                ),
            )
        except AutoIntegratorError as exc:
            detail = f"PR #{number} is gate-approved but {exc}; refusing to merge."
            unblock = (
                open_unblock_task(
                    candidate,
                    "task-brief-carry-forward-publication-failed",
                    detail,
                    settings,
                    runner,
                    root=status_root_dir,
                    execute=execute,
                )
                if open_unblock
                else None
            )
            return IntegrationResult(
                candidate.task_id,
                "blocked",
                detail,
                number,
                url,
                unblock,
                False,
                runner.commands[:],
            )
        if publication is None:
            detail = (
                f"PR #{number} has a carry-forward gate decision but no publishable "
                "task-brief proof; refusing to merge."
            )
            return IntegrationResult(
                candidate.task_id,
                "blocked",
                detail,
                number,
                url,
                dry_run=False,
                commands=runner.commands[:],
            )
        if publication.get("proof_published") or publication.get("workflow_dispatched"):
            detail = (
                f"PR #{number} published the task-brief carry-forward proof and dispatched "
                "the canonical review gate; waiting for that successor check to turn green."
            )
            return IntegrationResult(
                candidate.task_id,
                "waiting",
                detail,
                number,
                url,
                dry_run=False,
                commands=runner.commands[:],
            )

    checks = summarize_status_rollup(pr.get("statusCheckRollup"))
    if checks.state == "red":
        detail = f"PR #{number} has failing checks: {', '.join(checks.failing)}."
        unblock = (
            open_unblock_task(
                candidate,
                "ci-red",
                detail,
                settings,
                runner,
                root=status_root_dir,
                execute=execute,
            )
            if open_unblock
            else None
        )
        return IntegrationResult(
            candidate.task_id,
            "blocked",
            detail,
            number,
            url,
            unblock,
            not execute,
            runner.commands[:],
        )
    if checks.state in {"pending", "empty"}:
        detail = f"PR #{number} checks are {checks.state}; not merging."
        return IntegrationResult(
            candidate.task_id,
            "waiting",
            detail,
            number,
            url,
            dry_run=not execute,
            commands=runner.commands[:],
        )

    merge_state = normalize_state(pr.get("mergeStateStatus"))
    if merge_state and merge_state not in ALLOWED_PRE_REBASE_MERGE_STATES:
        detail = f"PR #{number} is not eligible: mergeStateStatus={merge_state}."
        unblock = (
            open_unblock_task(
                candidate,
                f"merge-state-{merge_state.lower()}",
                detail,
                settings,
                runner,
                root=status_root_dir,
                execute=execute,
            )
            if open_unblock
            else None
        )
        return IntegrationResult(
            candidate.task_id,
            "blocked",
            detail,
            number,
            url,
            unblock,
            not execute,
            runner.commands[:],
        )

    try:
        pushed, rebase_status = run_rebase_smoke(
            candidate,
            settings,
            runner,
            root=target_root,
            execute=execute,
            extra_smoke_commands=extra_smoke_commands,
            # Every policy is bound to the exact PR head whose gate/checks are
            # being evaluated. The sole merge owner never rewrites task heads.
            allow_push=False,
            exact_head=decision.head_oid,
        )
    except CommandFailure as exc:
        detail = f"Local smoke or git command failed for PR #{number}: {exc.output.strip() or exc.args_rendered}"
        unblock = (
            open_unblock_task(
                candidate,
                "smoke-failed",
                detail,
                settings,
                runner,
                root=status_root_dir,
                execute=execute,
            )
            if open_unblock
            else None
        )
        return IntegrationResult(
            candidate.task_id,
            "blocked",
            detail,
            number,
            url,
            unblock,
            not execute,
            runner.commands[:],
        )

    if rebase_status == "rebase_conflict":
        detail = f"PR #{number} does not rebase cleanly onto {candidate.target_branch}."
        unblock = (
            open_unblock_task(
                candidate,
                "rebase-conflict",
                detail,
                settings,
                runner,
                root=status_root_dir,
                execute=execute,
            )
            if open_unblock
            else None
        )
        return IntegrationResult(
            candidate.task_id,
            "blocked",
            detail,
            number,
            url,
            unblock,
            not execute,
            runner.commands[:],
        )

    if rebase_status == "exact_head_missing":
        detail = (
            f"PR #{number} passed the review gate without an exact approved head; "
            "refusing to smoke or merge an unbound branch ref."
        )
        unblock = (
            open_unblock_task(
                candidate,
                "exact-head-missing",
                detail,
                settings,
                runner,
                root=status_root_dir,
                execute=execute,
            )
            if open_unblock
            else None
        )
        return IntegrationResult(
            candidate.task_id,
            "blocked",
            detail,
            number,
            url,
            unblock,
            not execute,
            runner.commands[:],
        )

    if rebase_status == "rebase_required":
        detail = (
            f"PR #{number} needs a refreshed head to land on {candidate.target_branch}; "
            f"the integrator will not rewrite exact head {decision.head_oid}. "
            "Owner refreshes the branch before another integration pass."
        )
        return IntegrationResult(
            candidate.task_id,
            "waiting",
            detail,
            number,
            url,
            dry_run=not execute,
            commands=runner.commands[:],
        )

    if rebase_status == "exact_head_merge_conflict":
        # A real conflict, not mere staleness: merging the current dev tip
        # into the approved head does not apply cleanly. Rebasing to fix it
        # would move the head past what the reviewer saw, so this genuinely
        # needs the owner, not another wait cycle.
        detail = (
            f"PR #{number}'s evaluated head {decision.head_oid} no longer merges cleanly "
            f"with {candidate.target_branch}; a real conflict, not just staleness. "
            "Owner resolves it (new commit, new review) rather than waiting it out."
        )
        unblock = (
            open_unblock_task(
                candidate,
                "exact-head-merge-conflict",
                detail,
                settings,
                runner,
                root=status_root_dir,
                execute=execute,
            )
            if open_unblock
            else None
        )
        return IntegrationResult(
            candidate.task_id,
            "blocked",
            detail,
            number,
            url,
            unblock,
            not execute,
            runner.commands[:],
        )

    if pushed:
        detail = (
            f"Internal safety error: integration attempted to rewrite {candidate.branch}; "
            "refusing any auto-merge handoff."
        )
        return IntegrationResult(
            candidate.task_id,
            "blocked",
            detail,
            number,
            url,
            dry_run=not execute,
            commands=runner.commands[:],
        )

    merge_proc: subprocess.CompletedProcess[str] | None = None
    try:
        # Lock order is outer auto-integrator EX, then task-state SH, then
        # activity-audit SH. The live PR refresh, fresh gate/check readback and
        # synchronous exact-head merge are one indivisible authority window.
        with final_authority_read_locks(
            execute=execute,
            canonical_state_file=canonical_state_file,
            status_root=status_root_dir,
        ):
            with bounded_runner_timeout(runner, FINAL_MERGE_TIMEOUT_SECONDS):
                pr, decision, checks = revalidate_before_merge(
                    candidate,
                    settings,
                    runner,
                    root=target_root,
                    status_root=status_root_dir,
                    canonical_state_file=canonical_state_file,
                    prior_gate=gate,
                    prior_decision=decision,
                    prior_pr_number=number,
                )
                if execute:
                    merge_proc = runner.run(
                        merge_command(
                            candidate, number or 0, exact_head=decision.head_oid
                        ),
                        cwd=target_root,
                        check=False,
                        timeout=FINAL_MERGE_TIMEOUT_SECONDS,
                    )
    except FinalMergeRevalidationError as exc:
        # Canonical locks have exited before any task-state mutation below.
        action = "waiting" if exc.waiting else "blocked"
        detail = f"PR #{number} failed final merge revalidation: {exc.detail}"
        unblock = (
            open_unblock_task(
                candidate,
                exc.reason,
                detail,
                settings,
                runner,
                root=status_root_dir,
                execute=execute,
            )
            if action == "blocked" and open_unblock
            else None
        )
        return IntegrationResult(
            candidate.task_id,
            action,
            detail,
            number,
            url,
            unblock,
            not execute,
            runner.commands[:],
        )
    except subprocess.TimeoutExpired:
        detail = (
            f"PR #{number}'s final authority refresh or synchronous exact-head "
            f"REST merge timed out after "
            f"{FINAL_MERGE_TIMEOUT_SECONDS:g}s; its outcome is unknown and must "
            "be refreshed before any retry."
        )
        unblock = (
            open_unblock_task(
                candidate,
                "final-authority-timeout",
                detail,
                settings,
                runner,
                root=status_root_dir,
                execute=execute,
            )
            if open_unblock
            else None
        )
        return IntegrationResult(
            candidate.task_id,
            "blocked",
            detail,
            number,
            url,
            unblock,
            False,
            runner.commands[:],
        )

    if not execute:
        if gated:
            authority = str(decision.approval.get("authority") or "").strip()
            accepted_by = (
                f"accepted by Human/Ops at {decision.approved_at} through its distinct "
                "exact-head operator acceptance"
                if authority == "operator_exact_head"
                else (
                    f"approved by {decision.contract.get('reviewer')} at "
                    f"{decision.approved_at}"
                )
            )
            detail = (
                f"Dry-run: PR #{number} is green, {rebase_status}, and {accepted_by} "
                f"for exact head "
                f"{decision.head_oid}; would merge that exact head."
            )
        else:
            detail = (
                f"Dry-run: PR #{number} is green, {rebase_status}, and its "
                f"merge-then-review contract still permits exact head {decision.head_oid}; "
                "would merge that exact head directly."
            )
        detail += ignored_diagnostic_note(checks)
        return IntegrationResult(
            candidate.task_id,
            "would_merge",
            detail,
            number,
            url,
            dry_run=True,
            commands=runner.commands[:],
        )

    if merge_proc is None:
        raise AutoIntegratorError("live merge returned without a REST response")
    try:
        merge_payload = json.loads(merge_proc.stdout or "{}")
    except json.JSONDecodeError:
        merge_payload = {}
    merged_synchronously = (
        merge_proc.returncode == 0
        and isinstance(merge_payload, Mapping)
        and merge_payload.get("merged") is True
    )
    if not merged_synchronously:
        api_message = ""
        if isinstance(merge_payload, Mapping):
            api_message = str(merge_payload.get("message") or "").strip()
        api_message = (
            api_message
            or merge_proc.stderr.strip()
            or "GitHub did not return merged=true"
        )
        detail = (
            f"PR #{number}'s synchronous exact-head merge was refused: {api_message}. "
            "No auto-merge or merge-queue request was created; retry after the "
            "repository becomes directly mergeable."
        )
        return IntegrationResult(
            candidate.task_id,
            "waiting",
            detail,
            number,
            url,
            dry_run=False,
            commands=runner.commands[:],
        )
    _record_merge_integration_receipt(
        candidate,
        observation=integration_receipt.RECEIPT_OBSERVATION_PERFORMED_MERGE,
        pr=number,
        head_sha=decision.head_oid,
        merge_commit_sha=str(merge_payload.get("sha") or "") if isinstance(merge_payload, Mapping) else "",
        status_root=status_root_dir,
        status_file=canonical_state_file,
        config=config,
        lock_path=settings.lock_path,
    )
    operator_acceptance = candidate.raw_task.get("operator_acceptance")
    is_operator_exact_head = isinstance(operator_acceptance, Mapping) and str(
        operator_acceptance.get("mode") or ""
    ).strip() == "operator_exact_head"
    if gated:
        acceptance_label = (
            "Human/Ops exact-head accepted"
            if is_operator_exact_head
            else "reviewer-approved"
        )
        detail = (
            f"Merged the {acceptance_label} head {decision.head_oid} of PR #{number} into "
            f"{candidate.target_branch}; {post_merge_task_handoff(candidate)}."
        )
    else:
        detail = (
            f"Merged PR #{number} into {candidate.target_branch}; "
            f"{post_merge_task_handoff(candidate)}."
        )
    detail += ignored_diagnostic_note(checks)
    return IntegrationResult(
        candidate.task_id,
        "merged",
        detail,
        number,
        url,
        dry_run=False,
        commands=runner.commands[:],
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Safely integrate review_approved task PRs into dev.")
    parser.add_argument("--execute", action="store_true", help="Mutate git/GitHub/task state. Default is dry-run.")
    parser.add_argument("--task-id", help="Limit to one task id.")
    parser.add_argument(
        "--status-file",
        type=Path,
        default=None,
        help="Explicit status file. Defaults to ai-status.json under PANTHEON_STATUS_ROOT or repository root.",
    )
    parser.add_argument(
        "--config-file",
        type=Path,
        default=None,
        help="Explicit config file. Defaults to .orchestrator/config.json under PANTHEON_STATUS_ROOT or repository root.",
    )
    parser.add_argument("--max-tasks", type=int, help="Override max tasks per run.")
    parser.add_argument("--smoke-command", action="append", default=[], help="Extra or replacement smoke command.")
    parser.add_argument("--skip-smoke", action="store_true", help="Do not run configured smoke commands.")
    parser.add_argument(
        "--no-lock",
        action="store_true",
        help="Skip the integration lock for isolated dry-run tests only; incompatible with --execute.",
    )
    parser.add_argument("--no-open-unblock", action="store_true", help="Do not create unblock tasks for blockers.")
    parser.add_argument("--json", action="store_true", help="Print JSON result.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.execute and args.no_lock:
        parser.error("--execute requires the integration lock; --no-lock is dry-run test-only")
    if args.execute and (args.status_file is not None or args.config_file is not None):
        parser.error(
            "--execute rejects --status-file/--config-file overrides; live authority comes from the promoted watchdog config"
        )
    if args.execute and (args.skip_smoke or args.smoke_command):
        parser.error(
            "--execute rejects --skip-smoke/--smoke-command overrides; required smoke comes from the promoted live config"
        )
    runner = CommandRunner()
    if args.execute:
        live_config_raw = str(os.environ.get(LIVE_CONFIG_ENV) or "").strip()
        live_config_path = (
            Path(live_config_raw) if live_config_raw else DEFAULT_LIVE_CONFIG
        )
        try:
            status_file, status_root, settings, config_dict = resolve_execute_authority(
                live_config_path, runner, command_root=ROOT
            )
        except (ExecuteAuthorityError, OSError, ValueError) as exc:
            parser.error(f"live execute authority binding failed: {exc}")
    else:
        if args.status_file is not None:
            status_file = args.status_file.resolve()
            status_root = status_file.parent
        else:
            status_root = review_gate.resolve_status_root()
            status_file = status_root / "ai-status.json"
        if args.config_file is not None:
            config_path = args.config_file.resolve()
        else:
            status_config = status_root / ".orchestrator" / "config.json"
            config_path = status_config if status_config.exists() else DEFAULT_CONFIG
        settings = load_settings(config_path, status_root=status_root)
        config_dict = load_json(config_path, {})
        if not isinstance(config_dict, dict):
            config_dict = {}
    if args.max_tasks is not None:
        settings = Settings(**{**settings.__dict__, "max_tasks_per_run": args.max_tasks})
    paths = dict(config_dict.get("paths") or {})
    paths["status_file"] = str(status_file.resolve())
    config_dict["paths"] = paths
    smoke_commands = tuple() if args.skip_smoke else tuple(args.smoke_command) or settings.smoke_commands
    results: list[IntegrationResult] = []
    candidates: list[TaskCandidate] = []
    try:
        with lock_file(settings.lock_path, enabled=not args.no_lock):
            # Candidate selection and repository preflight happen while the same
            # lock that owns merge is held. A contending runner therefore cannot
            # carry stale canonical state across another runner's merge.
            state = load_json(status_file, {})
            discovered_candidates = integration_candidates(
                state,
                config=config_dict,
                task_branch_prefix=settings.task_branch_prefix,
                only_task_id=args.task_id,
                status_root=status_root,
            )
            max_tasks = max(1, int(settings.max_tasks_per_run))
            # The review gate reads canonical state from the same root that supplied
            # the candidates, so status file and audit can never disagree by binding.
            gate = ReviewGate(status_root=status_root, state=state)
            actionable_count = 0
            for candidate in discovered_candidates:
                candidates.append(candidate)
                result = integrate_candidate(
                    candidate,
                    settings,
                    runner,
                    root=candidate.repository_root,
                    status_root=status_root,
                    canonical_state_file=status_file,
                    execute=args.execute,
                    require_dedicated_integration_path=args.execute,
                    open_unblock=not args.no_open_unblock,
                    extra_smoke_commands=smoke_commands,
                    gate=gate,
                    config=config_dict,
                )
                results.append(result)
                if result_consumes_run_capacity(candidate, result):
                    actionable_count += 1
                    if actionable_count >= max_tasks:
                        break
    except IntegrationLockHeld as exc:
        payload = {
            "dry_run": not args.execute,
            "candidate_count": 0,
            "results": [],
            "skipped": True,
            "reason": "integration_lock_held",
            "detail": str(exc),
        }
        if args.json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print(f"auto-integrator skipped reason=integration_lock_held - {exc}")
        return 0
    except IntegrationLockError as exc:
        payload = {
            "dry_run": not args.execute,
            "candidate_count": 0,
            "results": [],
            "skipped": False,
            "reason": "integration_lock_error",
            "detail": str(exc),
        }
        if args.json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print(
                f"auto-integrator failed reason=integration_lock_error - {exc}",
                file=sys.stderr,
            )
        return 2

    payload = {
        "dry_run": not args.execute,
        "candidate_count": len(candidates),
        "results": [result.as_dict() for result in results],
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"auto-integrator dry_run={not args.execute} candidates={len(candidates)}")
        for result in results:
            suffix = f" PR #{result.pr_number}" if result.pr_number else ""
            print(f"- {result.task_id}: {result.action}{suffix} - {result.detail}")
    if any(result.action == "blocked" for result in results):
        return 2
    if any(result.action == "waiting" for result in results):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
