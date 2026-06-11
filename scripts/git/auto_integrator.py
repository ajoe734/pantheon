#!/usr/bin/env python3
"""Conservative serialized integrator for reviewed task PRs.

The auto-integrator closes the gap between a task reaching `review_approved`
and its PR actually landing in `dev`. It is intentionally narrow:

* one process at a time via `.orchestrator/auto-integrator.lock`;
* only `review_approved` tasks with `task/<TASK-ID>` PRs into `dev`;
* no conflict resolution;
* no branch-protection bypass;
* unblock tasks instead of stranded PRs when the safe path fails.

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


class AutoIntegratorError(RuntimeError):
    """Base auto-integrator failure."""


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
            "open",
            "--json",
            "number",
        ],
        cwd=root,
    )
    if not isinstance(listing, list) or not listing:
        return None
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
            "number,title,url,headRefName,baseRefName,isDraft,mergeStateStatus,reviewDecision,statusCheckRollup",
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
            pushed = False
            if execute and after != before:
                runner.run(
                    ["git", "push", "--force-with-lease", "origin", f"HEAD:{candidate.branch}"],
                    cwd=worktree,
                )
                pushed = True
            return pushed, "rebased_and_pushed" if pushed else "clean_rebase"
        finally:
            runner.run(["git", "worktree", "remove", "--force", str(worktree)], cwd=root, check=False)


def merge_command(number: int, settings: Settings, *, auto: bool) -> list[str]:
    args = ["gh", "pr", "merge", str(number)]
    if settings.merge_method == "squash":
        args.append("--squash")
    elif settings.merge_method == "rebase":
        args.append("--rebase")
    else:
        args.append("--merge")
    if auto:
        args.append("--auto")
    return args


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
) -> IntegrationResult:
    pr = fetch_pr_for_task(candidate, settings, runner, root=root)
    if pr is None:
        detail = f"No open PR found for {candidate.branch} -> {settings.dev_branch}."
        unblock = open_unblock_task(candidate, "missing-pr", detail, settings, runner, root=root, execute=execute) if open_unblock else None
        return IntegrationResult(candidate.task_id, "blocked", detail, unblock_task_id=unblock, dry_run=not execute, commands=runner.commands[:])
    number = pr_number(pr)
    url = str(pr.get("url") or "")
    problem = validate_pr(candidate, pr, settings)
    if problem:
        detail = f"PR #{number} is not eligible: {problem}."
        unblock = open_unblock_task(candidate, problem, detail, settings, runner, root=root, execute=execute) if open_unblock else None
        return IntegrationResult(candidate.task_id, "blocked", detail, number, url, unblock, not execute, runner.commands[:])
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
        )
    except CommandFailure as exc:
        detail = f"Local smoke or git command failed for PR #{number}: {exc.output.strip() or exc.args_rendered}"
        unblock = open_unblock_task(candidate, "smoke-failed", detail, settings, runner, root=root, execute=execute) if open_unblock else None
        return IntegrationResult(candidate.task_id, "blocked", detail, number, url, unblock, not execute, runner.commands[:])

    if rebase_status == "rebase_conflict":
        detail = f"PR #{number} does not rebase cleanly onto {settings.dev_branch}."
        unblock = open_unblock_task(candidate, "rebase-conflict", detail, settings, runner, root=root, execute=execute) if open_unblock else None
        return IntegrationResult(candidate.task_id, "blocked", detail, number, url, unblock, not execute, runner.commands[:])

    if not execute:
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
    runner.run(merge_command(number or 0, settings, auto=False), cwd=root)
    reconcile_done(candidate, pr, runner, root=root, execute=True)
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
