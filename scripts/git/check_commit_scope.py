#!/usr/bin/env python3
"""Pre-commit guard against shared-index sweep-in.

Pantheon workers share a single worktree, hence a single `.git/index`. If
worker A leaves files staged (e.g. interrupted commit) and worker B runs
`git commit`, B's commit absorbs A's files. This is the e06f5cf2 incident.

This guard fires from `.githooks/pre-commit` with the list of staged paths.
It applies two checks:

1. **Task scope manifest**: if the commit message under construction has a
   `Task-ID: <id>` trailer and `.orchestrator/task-briefs/<id>.md` declares
   a YAML-style `scope:` block, every staged path must live under one of
   those scope entries.

2. **Cross-dir heuristic**: if no manifest exists, count distinct top-level
   directories among staged files. > 3 top-level dirs without an explicit
   `Cross-Dir: yes` trailer is treated as suspicious. (Subjects starting
   with `Merge `, `Revert `, `wave-*`, `promote:`, `hotfix:`, or
   `OPS-GIT-WORKFLOW-` are exempt because they routinely span the repo.)

Bypass via `PANTHEON_SCOPE_CHECK_DISABLED=1`.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path


def _detect_repo_root() -> Path:
    """Return the current git repo's top-level directory, falling back to cwd."""
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
COMMIT_MSG_FILE = ROOT / ".git" / "COMMIT_EDITMSG"
TASK_BRIEF_DIR = ROOT / ".orchestrator" / "task-briefs"

EXEMPT_SUBJECT_PREFIXES = (
    "Merge ",
    "Revert ",
    "wave-merge:",
    "wave-close:",
    "wave-open:",
    "promote:",
    "hotfix:",
    "fixup!",
    "squash!",
    "OPS-GIT-WORKFLOW-",
    "OPS-DOC-",
    "OPS-REBASE-",
)

TASK_ID_RE = re.compile(r"^Task-ID:\s*(\S+)\s*$", re.MULTILINE)
CROSS_DIR_RE = re.compile(r"^Cross-Dir:\s*yes\s*$", re.MULTILINE | re.IGNORECASE)
SCOPE_SECTION_RE = re.compile(
    r"^scope:\s*\n((?:[ \t]*-[ \t]+\S.*\n)+)",
    re.MULTILINE,
)


def _read_commit_message() -> str:
    # commit-msg hook sets PANTHEON_COMMIT_MSG_FILE to argv[1]; prefer that.
    override = os.environ.get("PANTHEON_COMMIT_MSG_FILE")
    candidates: list[Path] = []
    if override:
        candidates.append(Path(override))
    candidates.append(COMMIT_MSG_FILE)
    for path in candidates:
        if path.exists():
            raw = path.read_text(encoding="utf-8", errors="replace")
            return "\n".join(line for line in raw.splitlines() if not line.startswith("#"))
    return ""


def _parse_brief_scope(task_id: str) -> list[str] | None:
    candidates = [
        TASK_BRIEF_DIR / f"{task_id}.md",
        TASK_BRIEF_DIR / f"{task_id.lower()}.md",
        TASK_BRIEF_DIR / f"{task_id.lower().replace('-', '_')}.md",
    ]
    brief = next((c for c in candidates if c.exists()), None)
    if brief is None:
        return None
    body = brief.read_text(encoding="utf-8", errors="replace")
    match = SCOPE_SECTION_RE.search(body)
    if not match:
        return None
    entries = [
        line.strip().lstrip("-").strip()
        for line in match.group(1).splitlines()
        if line.strip()
    ]
    return [e for e in entries if e]


def _path_within(path: str, scope_entry: str) -> bool:
    p = Path(path)
    e = Path(scope_entry)
    if p == e:
        return True
    try:
        p.relative_to(e)
        return True
    except ValueError:
        return False


def _top_level_dirs(paths: list[str]) -> set[str]:
    dirs: set[str] = set()
    for p in paths:
        first = Path(p).parts[0] if Path(p).parts else p
        dirs.add(first)
    return dirs


def main(argv: list[str]) -> int:
    if os.environ.get("PANTHEON_SCOPE_CHECK_DISABLED") == "1":
        return 0

    staged = argv[1:]
    if not staged:
        return 0

    message = _read_commit_message()
    subject_line = message.lstrip().split("\n", 1)[0] if message else ""
    if any(subject_line.startswith(p) for p in EXEMPT_SUBJECT_PREFIXES):
        return 0

    task_id_match = TASK_ID_RE.search(message)
    task_id = task_id_match.group(1) if task_id_match else None

    if task_id:
        scope = _parse_brief_scope(task_id)
        if scope:
            leaked = [
                p for p in staged
                if not any(_path_within(p, entry) for entry in scope)
            ]
            if leaked:
                print(
                    f"[scope-check] staged files leak outside the declared "
                    f"scope of {task_id}:",
                    file=sys.stderr,
                )
                for path in leaked:
                    print(f"  - {path}", file=sys.stderr)
                print(
                    f"\nDeclared scope in .orchestrator/task-briefs/{task_id}.md:",
                    file=sys.stderr,
                )
                for entry in scope:
                    print(f"  - {entry}", file=sys.stderr)
                print(
                    "\nFix: unstage the leaked files (`git restore --staged "
                    "<file>`) or, if intentional, update the brief's "
                    "scope: section.",
                    file=sys.stderr,
                )
                return 1
            return 0

    # Heuristic fallback: cross-dir spread.
    if CROSS_DIR_RE.search(message):
        return 0
    dirs = _top_level_dirs(staged)
    if len(dirs) > 3:
        print(
            f"[scope-check] commit spans {len(dirs)} top-level directories:",
            file=sys.stderr,
        )
        for d in sorted(dirs):
            print(f"  - {d}/", file=sys.stderr)
        print(
            "\nThis may indicate stale staging from another worker.\n"
            "Fix options:\n"
            "  1. `git restore --staged <unrelated>` to unstage what isn't yours.\n"
            "  2. Add the trailer `Cross-Dir: yes` if the spread is intentional.\n"
            "  3. Bypass with `PANTHEON_SCOPE_CHECK_DISABLED=1 git commit ...`.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
