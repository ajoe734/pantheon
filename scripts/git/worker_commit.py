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
import json
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


def _append_audit(payload: dict) -> None:
    try:
        ACTIVITY_LOG.parent.mkdir(parents=True, exist_ok=True)
        with ACTIVITY_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
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

    # Step 1: clear any existing staging. With a private index this is a no-op,
    # but stays cheap and idempotent.
    _git("restore", "--staged", "--", ".", env=env, check=False)

    # Step 2: stage only the declared scope. Use --intent-to-add for untracked
    # files? No — `git add` handles new files just fine. We add each entry
    # explicitly so a typo surfaces immediately.
    # Some tracked task artifacts live under repo-ignored mirror paths such as
    # execute-plans/. The explicit scope plus leak check below keeps this force
    # add narrow while allowing those tracked files through the safe wrapper.
    add_args = ["add", "-f", "--"] + scope
    proc = _git(*add_args, env=env, check=False)
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

    # Step 5: record audit entry.
    head_sha = _git("rev-parse", "HEAD", env=env).stdout.strip()
    audit = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "agent": args.llm_agent or "unknown",
        "type": "worker_commit",
        "task_id": args.task_id,
        "message": f"Worker commit {head_sha[:12]} recorded {len(staged)} staged file(s) for {args.task_id}.",
        "commit": head_sha,
        "scope": scope,
        "staged": staged,
        "index_file": args.index_file or None,
    }
    _append_audit(audit)
    return 0


if __name__ == "__main__":
    sys.exit(main())
