#!/usr/bin/env python3
"""Conservative serialized integrator for reviewed task PRs.

The auto-integrator closes the gap between a task reaching `review_approved`
and its PR actually landing in `dev`. It is intentionally narrow:

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
contract permits merge-then-review keep the previous integration behavior.

The default mode is dry-run. Pass `--execute` to mutate git/GitHub/task state.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence


sys.path.insert(0, str(Path(__file__).resolve().parent))

import task_review_merge_gate as review_gate  # noqa: E402  (local helper module)


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / ".orchestrator" / "config.json"
DEFAULT_STATUS = ROOT / "ai-status.json"
DEFAULT_DEV_BRANCH = "dev"
DEFAULT_TASK_PREFIX = "task/"
DEFAULT_LOCK = ".orchestrator/auto-integrator.lock"
DEFAULT_MERGE_METHOD = "merge"
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
ALLOWED_PRE_REBASE_MERGE_STATES = {"CLEAN", "HAS_HOOKS", "BEHIND", "UNKNOWN"}
ALLOWED_DIRECT_MERGE_STATES = {"CLEAN", "HAS_HOOKS", "UNKNOWN"}
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
    merge_method: str = DEFAULT_MERGE_METHOD
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


@dataclass(frozen=True)
class CheckSummary:
    state: str
    total: int = 0
    failing: tuple[str, ...] = ()
    pending: tuple[str, ...] = ()


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
    ) -> review_gate.GateDecision:
        return review_gate.gate_for_task(
            candidate.task_id,
            pr,
            status_root=self.status_root,
            dev_branch=settings.dev_branch,
            task_branch_prefix=settings.task_branch_prefix,
            state=self.state,
            events=self.events,
        )


class AutoIntegratorError(RuntimeError):
    """Base auto-integrator failure."""


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

    def run(
        self,
        args: Sequence[str],
        *,
        cwd: Path = ROOT,
        check: bool = True,
        env: Mapping[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        command = [str(arg) for arg in args]
        self.commands.append(command)
        result = subprocess.run(
            command,
            cwd=str(cwd),
            env=dict(env) if env is not None else None,
            capture_output=True,
            text=True,
        )
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
    ) -> subprocess.CompletedProcess[str]:
        self.commands.append(["sh", "-lc", command])
        result = subprocess.run(
            command,
            cwd=str(cwd),
            shell=True,
            env=dict(env) if env is not None else None,
            capture_output=True,
            text=True,
        )
        if check and result.returncode != 0:
            raise CommandFailure(command, result.returncode, result.stderr or result.stdout)
        return result


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return default
    return json.loads(text)


def load_settings(path: Path = DEFAULT_CONFIG) -> Settings:
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
        lock_path = ROOT / lock_path
    smoke = auto.get("smoke_commands") or ()
    if isinstance(smoke, str):
        smoke_commands = (smoke,)
    else:
        smoke_commands = tuple(str(item) for item in smoke if str(item).strip())
    return Settings(
        dev_branch=dev_branch,
        task_branch_prefix=task_prefix,
        lock_path=lock_path,
        merge_method=str(auto.get("merge_method") or DEFAULT_MERGE_METHOD),
        max_tasks_per_run=int(auto.get("max_tasks_per_run") or 1),
        smoke_commands=smoke_commands,
        unblock_owner=str(auto.get("unblock_owner") or "").strip() or None,
        unblock_reviewer=str(auto.get("unblock_reviewer") or "").strip() or None,
    )


def review_approved_candidates(
    state: Mapping[str, Any],
    *,
    task_branch_prefix: str = DEFAULT_TASK_PREFIX,
    only_task_id: str | None = None,
) -> list[TaskCandidate]:
    tasks = state.get("tasks") if isinstance(state.get("tasks"), list) else []
    candidates: list[TaskCandidate] = []
    for raw in tasks:
        if not isinstance(raw, Mapping):
            continue
        task_id = str(raw.get("id") or "").strip()
        if not task_id or (only_task_id and task_id != only_task_id):
            continue
        if str(raw.get("status") or "").strip().lower() != "review_approved":
            continue
        owner = str(raw.get("owner") or "").strip()
        reviewer = str(raw.get("reviewer") or "").strip()
        if not owner or not reviewer:
            continue
        candidates.append(
            TaskCandidate(
                task_id=task_id,
                title=str(raw.get("title") or task_id).strip(),
                owner=owner,
                reviewer=reviewer,
                branch=f"{task_branch_prefix}{task_id}",
            )
        )
    return candidates


def normalize_state(value: Any) -> str:
    return str(value or "").strip().upper().replace("-", "_")


def check_name(item: Mapping[str, Any]) -> str:
    for key in ("name", "context", "workflowName"):
        value = item.get(key)
        if value:
            return str(value)
    return "unnamed-check"


def summarize_status_rollup(rollup: Any) -> CheckSummary:
    if not isinstance(rollup, list) or not rollup:
        return CheckSummary("empty")
    failing: list[str] = []
    pending: list[str] = []
    for item in rollup:
        if not isinstance(item, Mapping):
            pending.append("malformed-check")
            continue
        values = [
            normalize_state(item.get("conclusion")),
            normalize_state(item.get("state")),
            normalize_state(item.get("status")),
        ]
        values = [value for value in values if value]
        if any(value in FAILURE_VALUES for value in values):
            failing.append(check_name(item))
            continue
        if any(value in PENDING_VALUES for value in values):
            pending.append(check_name(item))
            continue
        if any(value in SUCCESS_VALUES for value in values):
            continue
        # GitHub CheckRun often reports status=COMPLETED with a SUCCESS
        # conclusion. If conclusion is absent, treat COMPLETED as pending-ish
        # rather than silently green.
        pending.append(check_name(item))
    if failing:
        return CheckSummary("red", len(rollup), tuple(failing), tuple(pending))
    if pending:
        return CheckSummary("pending", len(rollup), (), tuple(pending))
    return CheckSummary("green", len(rollup))


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
            settings.dev_branch,
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
    return details if isinstance(details, Mapping) else None


def validate_pr(candidate: TaskCandidate, pr: Mapping[str, Any], settings: Settings) -> str | None:
    if bool(pr.get("isDraft")):
        return "pr_is_draft"
    if str(pr.get("headRefName") or "") != candidate.branch:
        return "head_branch_mismatch"
    if str(pr.get("baseRefName") or "") != settings.dev_branch:
        return "base_branch_mismatch"
    return None


def pr_merge_commit_oid(pr: Mapping[str, Any]) -> str:
    merge_commit = pr.get("mergeCommit")
    if not isinstance(merge_commit, Mapping):
        return ""
    return str(merge_commit.get("oid") or "").strip()


def target_contains_commit(
    oid: str,
    settings: Settings,
    runner: CommandRunner,
    *,
    root: Path = ROOT,
) -> bool:
    runner.run(["git", "fetch", "origin", settings.dev_branch, "--quiet"], cwd=root)
    result = runner.run(
        ["git", "merge-base", "--is-ancestor", oid, f"origin/{settings.dev_branch}"],
        cwd=root,
        check=False,
    )
    return result.returncode == 0


@contextmanager
def lock_file(lock_path: Path, *, enabled: bool = True) -> Iterator[None]:
    if not enabled:
        yield
        return
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise AutoIntegratorError(f"auto-integrator lock is already held: {lock_path}") from exc
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"pid": os.getpid(), "created_at": int(time.time())}) + "\n")
        yield
    finally:
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


def fetch_refs(candidate: TaskCandidate, settings: Settings, runner: CommandRunner, *, root: Path) -> None:
    runner.run(["git", "fetch", "origin", settings.dev_branch, "--quiet"], cwd=root)
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
) -> tuple[bool, str]:
    fetch_refs(candidate, settings, runner, root=root)
    commands = tuple(extra_smoke_commands) or settings.smoke_commands
    with tempfile.TemporaryDirectory(prefix=f"pantheon-integrate-{candidate.task_id}-") as tmp:
        worktree = Path(tmp)
        runner.run(["git", "worktree", "add", "--detach", str(worktree), f"origin/{candidate.branch}"], cwd=root)
        try:
            before = runner.run(["git", "rev-parse", "HEAD"], cwd=worktree).stdout.strip()
            rebase = runner.run(["git", "rebase", f"origin/{settings.dev_branch}"], cwd=worktree, check=False)
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
    number: int,
    settings: Settings,
    *,
    auto: bool,
    match_head_commit: str = "",
) -> list[str]:
    args = ["gh", "pr", "merge", str(number)]
    if settings.merge_method == "squash":
        args.append("--squash")
    elif settings.merge_method == "rebase":
        args.append("--rebase")
    else:
        args.append("--merge")
    if auto:
        args.append("--auto")
    if match_head_commit:
        # GitHub refuses the merge if the head moved between the gate decision
        # and this call, which closes the concurrent-finalize race.
        args.extend(["--match-head-commit", match_head_commit])
    return args


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


def reconcile_done(
    candidate: TaskCandidate,
    pr: Mapping[str, Any],
    runner: CommandRunner,
    *,
    root: Path,
    execute: bool,
) -> None:
    if not execute:
        return
    number = pr_number(pr)
    message = f"Auto-integrator merged PR #{number} into dev; task branch is integrated."
    env = os.environ.copy()
    env["AI_NAME"] = candidate.owner
    runner.run(
        [sys.executable, "scripts/ai_status.py", "done", candidate.task_id, message],
        cwd=root,
        env=env,
    )


def unblock_task_id(task_id: str, reason: str) -> str:
    safe_reason = "".join(ch if ch.isalnum() else "-" for ch in reason.upper()).strip("-")
    return f"INTEGRATION-UNBLOCK-{task_id}-{safe_reason}"[:96]


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
    env = os.environ.copy()
    env["AI_NAME"] = "AutoIntegrator"
    env["TASK_PHASE"] = "Auto-integrator unblock"
    env["TASK_DEPENDS_ON"] = candidate.task_id
    env["TASK_SUMMARY_ZH"] = (
        f"auto-integrator 無法安全整合 {candidate.task_id}: {reason}. "
        "請修正 PR/rebase/CI 後交回整合。"
    )
    env["TASK_ACCEPTANCE"] = (
        f"Root cause for {candidate.task_id} integration blocker is documented,"
        " original PR is updated or superseded, task no longer strands in review_approved"
    )
    env["TASK_ARTIFACTS"] = "ai-status.json,.orchestrator/task-briefs,scripts/git/auto_integrator.py"
    env["TASK_AUTO_CREATED_BY"] = "auto_integrator"
    env["TASK_AUTO_GENERATED"] = "true"
    runner.run(
        [
            sys.executable,
            "scripts/ai_status.py",
            "assign",
            task_id,
            owner,
            reviewer,
            f"Unblock integration for {candidate.task_id}: {reason}",
        ],
        cwd=root,
        env=env,
    )
    runner.run(
        [
            sys.executable,
            "scripts/ai_status.py",
            "progress",
            task_id,
            detail[:500],
        ],
        cwd=root,
        env=env,
        check=False,
    )
    return task_id


def integrate_candidate(
    candidate: TaskCandidate,
    settings: Settings,
    runner: CommandRunner,
    *,
    root: Path = ROOT,
    execute: bool = False,
    open_unblock: bool = True,
    extra_smoke_commands: Sequence[str] = (),
    gate: ReviewGate | None = None,
) -> IntegrationResult:
    gate = gate or ReviewGate()
    try:
        pr = fetch_pr_for_task(candidate, settings, runner, root=root)
    except AmbiguousPullRequests as exc:
        detail = f"{exc}; refusing to choose a head for {candidate.task_id}."
        unblock = (
            open_unblock_task(candidate, "ambiguous-open-prs", detail, settings, runner, root=root, execute=execute)
            if open_unblock
            else None
        )
        return IntegrationResult(
            candidate.task_id, "blocked", detail, unblock_task_id=unblock, dry_run=not execute, commands=runner.commands[:]
        )
    if pr is None:
        merged_pr = fetch_pr_for_task(candidate, settings, runner, root=root, state="merged")
        if merged_pr is not None:
            number = pr_number(merged_pr)
            url = str(merged_pr.get("url") or "")
            problem = validate_pr(candidate, merged_pr, settings)
            if problem:
                detail = f"Merged PR #{number} is not eligible for reconciliation: {problem}."
                unblock = open_unblock_task(candidate, problem, detail, settings, runner, root=root, execute=execute) if open_unblock else None
                return IntegrationResult(candidate.task_id, "blocked", detail, number, url, unblock, not execute, runner.commands[:])
            oid = pr_merge_commit_oid(merged_pr)
            if not oid:
                detail = f"Merged PR #{number} has no merge commit oid; cannot reconcile {candidate.task_id}."
                unblock = open_unblock_task(candidate, "merged-pr-no-merge-commit", detail, settings, runner, root=root, execute=execute) if open_unblock else None
                return IntegrationResult(candidate.task_id, "blocked", detail, number, url, unblock, not execute, runner.commands[:])
            if not target_contains_commit(oid, settings, runner, root=root):
                detail = f"Merged PR #{number} merge commit {oid} is not in origin/{settings.dev_branch}; not reconciling."
                return IntegrationResult(candidate.task_id, "waiting", detail, number, url, dry_run=not execute, commands=runner.commands[:])
            merged_decision = gate.decide(candidate, merged_pr, settings)
            if merged_decision.policy == review_gate.POLICY_REVIEW_BEFORE_MERGE and not merged_decision.allow_merge:
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
                        root=root,
                        execute=execute,
                    )
                    if open_unblock
                    else None
                )
                return IntegrationResult(candidate.task_id, "blocked", detail, number, url, unblock, not execute, runner.commands[:])
            if not execute:
                detail = f"Dry-run: PR #{number} is already merged into {settings.dev_branch}; would reconcile {candidate.task_id} to done."
                return IntegrationResult(candidate.task_id, "would_reconcile_done", detail, number, url, dry_run=True, commands=runner.commands[:])
            reconcile_done(candidate, merged_pr, runner, root=root, execute=True)
            detail = f"Reconciled {candidate.task_id} to done after PR #{number} was already merged into {settings.dev_branch}."
            return IntegrationResult(candidate.task_id, "reconciled_done", detail, number, url, dry_run=False, commands=runner.commands[:])

        detail = f"No open or merged PR found for {candidate.branch} -> {settings.dev_branch}."
        unblock = open_unblock_task(candidate, "missing-pr", detail, settings, runner, root=root, execute=execute) if open_unblock else None
        return IntegrationResult(candidate.task_id, "blocked", detail, unblock_task_id=unblock, dry_run=not execute, commands=runner.commands[:])
    number = pr_number(pr)
    url = str(pr.get("url") or "")
    problem = validate_pr(candidate, pr, settings)
    if problem:
        detail = f"PR #{number} is not eligible: {problem}."
        unblock = open_unblock_task(candidate, problem, detail, settings, runner, root=root, execute=execute) if open_unblock else None
        return IntegrationResult(candidate.task_id, "blocked", detail, number, url, unblock, not execute, runner.commands[:])

    # Canonical review-before-merge gate. This runs before the CI and merge
    # state probes so a premature auto-merge request is revoked immediately
    # rather than after the checks happen to turn green.
    decision = gate.decide(candidate, pr, settings)
    gated = decision.policy == review_gate.POLICY_REVIEW_BEFORE_MERGE
    # A gated PR must never hold an auto-merge request, whatever the gate went
    # on to decide and whatever GitHub currently thinks of its merge state. PR
    # #4201 sat BEHIND with auto-merge armed and no approval: only the stale
    # base was holding it back, and it would have merged the moment the base
    # caught up. Revoke first, then classify.
    revocation_command_succeeded = False
    revocation_read_error = ""
    revocation_attempted = gated and has_auto_merge_request(pr)
    if revocation_attempted:
        revocation_command_succeeded = disable_auto_merge(number, runner, root=root, execute=execute)
        if execute:
            try:
                live_auto_merge_request = read_auto_merge_request(number, runner, root=root)
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
                root=root,
                execute=execute,
            )
            if open_unblock
            else None
        )
        return IntegrationResult(candidate.task_id, "blocked", detail, number, url, unblock, not execute, runner.commands[:])

    if revocation_attempted and execute and (
        revocation_read_error or has_auto_merge_request(pr)
    ):
        # The gate approved this head, but the post-revocation readback is
        # unavailable or still shows the merge grant armed. The command's exit
        # status is diagnostic only: a zero can leave the grant armed, while a
        # nonzero can race with another actor that already turned it off.
        # Proceeding would emit a direct `--match-head-commit` merge while
        # GitHub may independently hold authority to land whatever head stands
        # next. Stop before any merge call is emitted.
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
                root=root,
                execute=execute,
            )
            if open_unblock
            else None
        )
        return IntegrationResult(
            candidate.task_id, "blocked", detail, number, url, unblock, not execute, runner.commands[:]
        )

    checks = summarize_status_rollup(pr.get("statusCheckRollup"))
    if checks.state == "red":
        detail = f"PR #{number} has failing checks: {', '.join(checks.failing)}."
        unblock = open_unblock_task(candidate, "ci-red", detail, settings, runner, root=root, execute=execute) if open_unblock else None
        return IntegrationResult(candidate.task_id, "blocked", detail, number, url, unblock, not execute, runner.commands[:])
    if checks.state in {"pending", "empty"}:
        detail = f"PR #{number} checks are {checks.state}; not merging."
        return IntegrationResult(candidate.task_id, "waiting", detail, number, url, dry_run=not execute, commands=runner.commands[:])

    merge_state = normalize_state(pr.get("mergeStateStatus"))
    if merge_state and merge_state not in ALLOWED_PRE_REBASE_MERGE_STATES:
        detail = f"PR #{number} is not eligible: mergeStateStatus={merge_state}."
        unblock = open_unblock_task(candidate, f"merge-state-{merge_state.lower()}", detail, settings, runner, root=root, execute=execute) if open_unblock else None
        return IntegrationResult(candidate.task_id, "blocked", detail, number, url, unblock, not execute, runner.commands[:])

    try:
        pushed, rebase_status = run_rebase_smoke(
            candidate,
            settings,
            runner,
            root=root,
            execute=execute,
            extra_smoke_commands=extra_smoke_commands,
            # A gated PR may never be force-pushed: replacing the head would
            # discard the exact commit the reviewer approved.
            allow_push=not gated,
        )
    except CommandFailure as exc:
        detail = f"Local smoke or git command failed for PR #{number}: {exc.output.strip() or exc.args_rendered}"
        unblock = open_unblock_task(candidate, "smoke-failed", detail, settings, runner, root=root, execute=execute) if open_unblock else None
        return IntegrationResult(candidate.task_id, "blocked", detail, number, url, unblock, not execute, runner.commands[:])

    if rebase_status == "rebase_conflict":
        detail = f"PR #{number} does not rebase cleanly onto {settings.dev_branch}."
        unblock = open_unblock_task(candidate, "rebase-conflict", detail, settings, runner, root=root, execute=execute) if open_unblock else None
        return IntegrationResult(candidate.task_id, "blocked", detail, number, url, unblock, not execute, runner.commands[:])

    if gated and rebase_status == "rebase_required":
        # Landing this PR needs a new head, and no reviewer has seen that head.
        detail = (
            f"PR #{number} needs a refreshed head to land on {settings.dev_branch}; "
            f"the approval of {decision.head_oid} would not cover it. "
            "Owner refreshes the branch and the assigned reviewer re-approves the new head."
        )
        return IntegrationResult(candidate.task_id, "waiting", detail, number, url, dry_run=not execute, commands=runner.commands[:])

    if not execute:
        if gated:
            detail = (
                f"Dry-run: PR #{number} is green, {rebase_status}, and approved by "
                f"{decision.contract.get('reviewer')} at {decision.approved_at} for exact head "
                f"{decision.head_oid}; would merge that exact head."
            )
        else:
            detail = f"Dry-run: PR #{number} is green and {rebase_status}; would merge or enable auto-merge."
        return IntegrationResult(candidate.task_id, "would_merge", detail, number, url, dry_run=True, commands=runner.commands[:])

    if pushed:
        runner.run(merge_command(number or 0, settings, auto=True), cwd=root)
        detail = f"Rebased {candidate.branch}, pushed updated head, and enabled auto-merge on PR #{number}."
        return IntegrationResult(candidate.task_id, "auto_merge_enabled", detail, number, url, dry_run=False, commands=runner.commands[:])

    merge_state = normalize_state(pr.get("mergeStateStatus"))
    if merge_state and merge_state not in ALLOWED_DIRECT_MERGE_STATES:
        detail = f"PR #{number} is green but mergeStateStatus={merge_state}; waiting instead of merging."
        return IntegrationResult(candidate.task_id, "waiting", detail, number, url, dry_run=False, commands=runner.commands[:])
    runner.run(
        merge_command(
            number or 0,
            settings,
            auto=False,
            match_head_commit=decision.head_oid if gated else "",
        ),
        cwd=root,
    )
    reconcile_done(candidate, pr, runner, root=root, execute=True)
    if gated:
        detail = (
            f"Merged the reviewer-approved head {decision.head_oid} of PR #{number} into "
            f"{settings.dev_branch} and reconciled {candidate.task_id} to done."
        )
    else:
        detail = f"Merged PR #{number} into {settings.dev_branch} and reconciled {candidate.task_id} to done."
    return IntegrationResult(candidate.task_id, "merged", detail, number, url, dry_run=False, commands=runner.commands[:])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Safely integrate review_approved task PRs into dev.")
    parser.add_argument("--execute", action="store_true", help="Mutate git/GitHub/task state. Default is dry-run.")
    parser.add_argument("--task-id", help="Limit to one task id.")
    parser.add_argument("--status-file", type=Path, default=DEFAULT_STATUS)
    parser.add_argument("--config-file", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--max-tasks", type=int, help="Override max tasks per run.")
    parser.add_argument("--smoke-command", action="append", default=[], help="Extra or replacement smoke command.")
    parser.add_argument("--skip-smoke", action="store_true", help="Do not run configured smoke commands.")
    parser.add_argument("--no-lock", action="store_true", help="Skip the integration lock. Intended for tests only.")
    parser.add_argument("--no-open-unblock", action="store_true", help="Do not create unblock tasks for blockers.")
    parser.add_argument("--json", action="store_true", help="Print JSON result.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = load_settings(args.config_file)
    if args.max_tasks is not None:
        settings = Settings(**{**settings.__dict__, "max_tasks_per_run": args.max_tasks})
    state = load_json(args.status_file, {})
    candidates = review_approved_candidates(
        state,
        task_branch_prefix=settings.task_branch_prefix,
        only_task_id=args.task_id,
    )
    max_tasks = max(1, int(settings.max_tasks_per_run))
    candidates = candidates[:max_tasks]
    runner = CommandRunner()
    smoke_commands = tuple() if args.skip_smoke else tuple(args.smoke_command) or settings.smoke_commands
    # The review gate reads canonical state from the same root that supplied
    # the candidates, so status file and audit can never disagree by binding.
    gate = ReviewGate(status_root=args.status_file.resolve().parent, state=state)
    results: list[IntegrationResult] = []
    with lock_file(settings.lock_path, enabled=not args.no_lock):
        for candidate in candidates:
            results.append(
                integrate_candidate(
                    candidate,
                    settings,
                    runner,
                    root=ROOT,
                    execute=args.execute,
                    open_unblock=not args.no_open_unblock,
                    extra_smoke_commands=smoke_commands,
                    gate=gate,
                )
            )

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
    if any(result.action in {"waiting", "auto_merge_enabled"} for result in results):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
