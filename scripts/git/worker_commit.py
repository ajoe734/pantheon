#!/usr/bin/env python3
"""Worker-safe commit wrapper.

Pantheon auto workers share a single worktree, hence a single `.git/index`.
If a previous worker left files staged (e.g. a commit got interrupted), the
next worker's `git commit` will silently absorb those files. This is the
sweep-in incident pattern observed on 2026-05-16 (commit e06f5cf2).

This wrapper enforces the safe sequence:

  1. Clear ALL existing staging (`git restore --staged .`).
  2. Stage ONLY the explicit paths declared via `--scope`.
  3. Verify the resulting index matches `--scope` (defensive; rejects races).
  4. `git commit -F <message-file>`.
  5. Record the staged path set into the activity log for audit.

Optionally accepts `--index-file <path>` to use a private staging index via
`GIT_INDEX_FILE`, which isolates this commit from other workers entirely.
Recommended for any orchestrator-managed bg worker.

CLI:

  worker_commit.py \\
      --task-id OSS-FINRL-001 \\
      --message-file /tmp/msg.txt \\
      --scope services/research/finrl/adapter.py \\
              services/research/finrl/Dockerfile \\
      [--index-file /tmp/git-index-<run_id>]

Exits non-zero with a clear diagnostic if the worker's intended scope
differs from what would actually be committed.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path


def _detect_repo_root() -> Path:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        if out:
            return Path(out)
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    return Path.cwd()


ROOT = _detect_repo_root()
STATUS_ROOT = Path(os.environ.get("PANTHEON_STATUS_ROOT") or ROOT).resolve()
ACTIVITY_LOG = STATUS_ROOT / "ai-activity-log.jsonl"
# The commit wrapper is executed from the worker's *target* repository.  For
# cross-repository work (for example a Pantheon worker committing in
# execute-plans), that repository does not contain Pantheon's orchestrator
# module.  Resolve the shared runtime explicitly instead of accidentally
# importing a foreign/missing ``common`` module from the target checkout.
_command_root = os.environ.get("PANTHEON_COMMAND_ROOT")
_runtime_orchestrator = (
    Path(_command_root).expanduser().resolve() / ".orchestrator"
    if _command_root
    else None
)
if _command_root:
    if not _runtime_orchestrator or not (_runtime_orchestrator / "common.py").is_file():
        raise ModuleNotFoundError(
            "PANTHEON_COMMAND_ROOT does not contain .orchestrator/common.py"
        )
    ORCHESTRATOR_DIR = _runtime_orchestrator
else:
    ORCHESTRATOR_DIR = ROOT / ".orchestrator"
if str(ORCHESTRATOR_DIR) not in sys.path:
    sys.path.insert(0, str(ORCHESTRATOR_DIR))

try:
    from common import write_activity_log
except ModuleNotFoundError as exc:
    if exc.name == "common":
        raise ModuleNotFoundError(
            "worker_commit.py requires Pantheon .orchestrator/common.py; "
            "set PANTHEON_COMMAND_ROOT to the command runtime when committing "
            "from a different repository"
        ) from exc
    raise


def _git(*args: str, env: dict[str, str] | None = None, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        check=check,
        capture_output=True,
        text=True,
        cwd=ROOT,
        env=env,
    )


def _build_env(index_file: str | None) -> dict[str, str]:
    env = os.environ.copy()
    if index_file:
        Path(index_file).parent.mkdir(parents=True, exist_ok=True)
        env["GIT_INDEX_FILE"] = index_file
        # Seed the private index from the current HEAD so untracked files are
        # the only things added; tracked files start unmodified.
        head = _git("rev-parse", "HEAD").stdout.strip()
        _git("read-tree", head, env=env)
    return env


def _default_index_env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("GIT_INDEX_FILE", None)
    return env


def _should_refresh_default_index(index_file: str | None) -> bool:
    if not index_file:
        return False
    try:
        return STATUS_ROOT.resolve() != ROOT.resolve()
    except OSError:
        return False


def _refresh_default_index_after_private_commit(index_file: str | None) -> str | None:
    """Sync an isolated worker worktree's normal index after a private-index commit."""
    if not _should_refresh_default_index(index_file):
        return None
    proc = _git("read-tree", "HEAD", env=_default_index_env(), check=False)
    if proc.returncode != 0:
        return (proc.stderr or proc.stdout or "git read-tree HEAD failed").strip()
    return None


def _normalize_paths(paths: list[str]) -> list[str]:
    normalized: list[str] = []
    for raw in paths:
        candidate = Path(raw).resolve()
        try:
            rel = candidate.relative_to(ROOT)
        except ValueError:
            print(f"path outside repo: {raw}", file=sys.stderr)
            sys.exit(2)
        normalized.append(rel.as_posix())
    return sorted(set(normalized))


def _staged_paths(env: dict[str, str]) -> list[str]:
    out = _git("diff", "--cached", "--name-only", env=env).stdout.strip()
    return sorted(p for p in out.splitlines() if p)


def _is_ignored_path(path: str, env: dict[str, str]) -> bool:
    return _git(
        "check-ignore",
        "--no-index",
        "-q",
        "--",
        path,
        env=env,
        check=False,
    ).returncode == 0


def _stage_scope(scope: list[str], env: dict[str, str]) -> subprocess.CompletedProcess:
    normal_scope: list[str] = []
    forced_files: list[str] = []
    for path in scope:
        path_on_disk = ROOT / path
        if _is_ignored_path(path, env):
            if path_on_disk.is_dir():
                return subprocess.CompletedProcess(
                    ["git", "add", "--", path],
                    returncode=2,
                    stdout="",
                    stderr=(
                        "Refusing to force-add ignored directory scope "
                        f"{path!r}. Pass explicit file paths for ignored "
                        "task artifacts instead.\n"
                    ),
                )
            forced_files.append(path)
        else:
            normal_scope.append(path)

    if normal_scope:
        proc = _git("add", "--", *normal_scope, env=env, check=False)
        if proc.returncode != 0:
            return proc
    if forced_files:
        proc = _git("add", "-f", "--", *forced_files, env=env, check=False)
        if proc.returncode != 0:
            return proc
    return subprocess.CompletedProcess(
        ["git", "add", "--", *scope],
        returncode=0,
        stdout="",
        stderr="",
    )


def _append_audit(payload: dict) -> None:
    try:
        write_activity_log(
            {"paths": {"activity_log": str(ACTIVITY_LOG)}},
            payload,
        )
    except OSError as exc:
        print(f"warning: could not append audit log: {exc}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--message-file", required=True)
    parser.add_argument(
        "--scope",
        nargs="+",
        required=True,
        help="Explicit list of repo-relative files/dirs this commit owns.",
    )
    parser.add_argument(
        "--index-file",
        default=None,
        help="Private GIT_INDEX_FILE path; recommended for bg workers.",
    )
    parser.add_argument(
        "--llm-agent",
        default=os.environ.get("AI_NAME") or os.environ.get("PANTHEON_LLM_AGENT"),
        help="Override the LLM-Agent recorded in the audit entry.",
    )
    parser.add_argument(
        "--allow-empty",
        action="store_true",
        help="Pass through --allow-empty to git commit (rarely needed).",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    scope = _normalize_paths(args.scope)
    env = _build_env(args.index_file)

    # Preflight: validate message file and subject line length before staging or committing.
    msg_path = Path(args.message_file)
    if not msg_path.exists():
        print(f"ERROR: message file not found: {args.message_file}", file=sys.stderr)
        return 5

    try:
        msg_text = msg_path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"ERROR: cannot read message file {args.message_file}: {exc}", file=sys.stderr)
        return 5

    msg_lines = [l for l in msg_text.splitlines() if not l.startswith("#")]
    subject = msg_lines[0].strip() if msg_lines else ""

    if not subject:
        print(f"ERROR: commit message in {args.message_file} has an empty subject line.", file=sys.stderr)
        return 5

    if len(subject) > 72:
        print(
            f"ERROR: commit subject exceeds 72 characters ({len(subject)} chars): '{subject}'",
            file=sys.stderr,
        )
        print(
            "Hint: Compact the subject line (e.g. abbreviate scope/summary) to <= 72 chars. "
            f"Keep full Task-ID in trailer (Task-ID: {args.task_id}).",
            file=sys.stderr,
        )
        return 5

    # Step 1: clear any existing staging. With a private index this is a no-op,
    # but stays cheap and idempotent.
    _git("restore", "--staged", "--", ".", env=env, check=False)

    # Step 2: stage only the declared scope. Use --intent-to-add for untracked
    # files? No — `git add` handles new files just fine. We add each entry
    # explicitly so a typo surfaces immediately.
    # Some tracked task artifacts live under repo-ignored mirror paths such as
    # execute-plans/. Force-add only ignored file paths named explicitly in
    # --scope; never force-add directory scopes because that can sweep ignored
    # build artifacts into the commit.
    proc = _stage_scope(scope, env)
    if proc.returncode != 0:
        print("git add failed:")
        print(proc.stderr, file=sys.stderr)
        return proc.returncode

    # Step 3: verify the staged set matches the declared scope (defensive).
    staged = _staged_paths(env)
    declared_set = set(scope)
    staged_set = set(staged)

    # Expand scope: a declared directory entry permits anything under it.
    def _within_scope(path: str) -> bool:
        if path in declared_set:
            return True
        p = Path(path)
        for entry in declared_set:
            ep = Path(entry)
            try:
                p.relative_to(ep)
                return True
            except ValueError:
                continue
        return False

    leaked = sorted(p for p in staged_set if not _within_scope(p))
    if leaked:
        print(
            "ERROR: staged files leak outside declared scope. Refusing to commit.",
            file=sys.stderr,
        )
        print("Declared scope:", ", ".join(scope) or "(empty)", file=sys.stderr)
        print("Leaked staged:", ", ".join(leaked), file=sys.stderr)
        print(
            "Hint: another worker may have left files staged in the shared "
            "index. Either re-run with --index-file, or `git restore --staged"
            " --` the unrelated entries manually.",
            file=sys.stderr,
        )
        return 3

    if not staged_set:
        print("ERROR: nothing staged after applying scope; refusing empty commit.", file=sys.stderr)
        return 4

    if args.dry_run:
        print("[dry-run] staged set matches scope; would commit:")
        for p in staged:
            print(f"  - {p}")
        return 0

    # Step 4: commit.
    commit_args = ["commit", "-F", args.message_file]
    if args.allow_empty:
        commit_args.append("--allow-empty")
    commit_proc = _git(*commit_args, env=env, check=False)
    if commit_proc.returncode != 0:
        sys.stderr.write(commit_proc.stderr)
        sys.stdout.write(commit_proc.stdout)
        return commit_proc.returncode
    sys.stdout.write(commit_proc.stdout)
    sys.stderr.write(commit_proc.stderr)
    refresh_error = _refresh_default_index_after_private_commit(args.index_file)
    if refresh_error:
        print(f"warning: could not refresh default index after private-index commit: {refresh_error}", file=sys.stderr)

    # Step 5: record audit entry.
    head_sha = _git("rev-parse", "HEAD", env=env).stdout.strip()
    audit = {
        "event_id": f"worker-commit-{head_sha}",
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "agent": args.llm_agent or "unknown",
        "type": "worker_commit",
        "task_id": args.task_id,
        "message": f"Worker commit {head_sha[:12]} recorded {len(staged)} staged file(s) for {args.task_id}.",
        "commit": head_sha,
        "scope": scope,
        "staged": staged,
        "index_file": args.index_file or None,
        "default_index_refreshed": bool(args.index_file and not refresh_error and _should_refresh_default_index(args.index_file)),
        "default_index_refresh_error": refresh_error,
    }
    _append_audit(audit)
    return 0


if __name__ == "__main__":
    sys.exit(main())
