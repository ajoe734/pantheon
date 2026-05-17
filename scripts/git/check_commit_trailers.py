#!/usr/bin/env python3
"""Verify that commit messages on a branch carry required Pantheon trailers.

Used by:
  - .githooks/commit-msg     (one commit, the staged message)
  - .github/workflows/wave-ci.yml (range of commits in a PR)

Reads required trailers from .orchestrator/config.json:
  wave_workflow.wave_merge.require_commit_trailers
  wave_workflow.wave_merge.subject_prefix_required

Default required trailers: LLM-Agent, Task-ID, Reviewer, Wave.

CLI:
  check_commit_trailers.py --message-file <path>
  check_commit_trailers.py --range <base>..<head>
  check_commit_trailers.py --rev <sha>
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG_FILE = ROOT / ".orchestrator" / "config.json"

DEFAULT_REQUIRED = ("LLM-Agent", "Task-ID", "Reviewer", "Wave")
SUBJECT_PATTERN = re.compile(r"^[A-Z][A-Z0-9-]*[A-Z0-9]:\s+\S")

# Subjects that legitimately bypass the task-id prefix rule.
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
    "Initial commit",
)


def load_settings() -> tuple[tuple[str, ...], bool]:
    if not CONFIG_FILE.exists():
        return DEFAULT_REQUIRED, True
    try:
        payload = json.loads(CONFIG_FILE.read_text())
    except json.JSONDecodeError:
        return DEFAULT_REQUIRED, True
    wm = (payload.get("wave_workflow") or {}).get("wave_merge") or {}
    trailers = wm.get("require_commit_trailers")
    if not isinstance(trailers, list) or not trailers:
        trailers = list(DEFAULT_REQUIRED)
    prefix_required = bool(wm.get("subject_prefix_required", True))
    return tuple(str(t) for t in trailers), prefix_required


def parse_trailers(body: str) -> dict[str, str]:
    trailers: dict[str, str] = {}
    for line in body.splitlines():
        stripped = line.rstrip()
        m = re.match(r"^([A-Za-z][A-Za-z0-9-]*):\s+(.+)$", stripped)
        if m:
            trailers[m.group(1)] = m.group(2)
    return trailers


def is_exempt_subject(subject: str) -> bool:
    return any(subject.startswith(p) for p in EXEMPT_SUBJECT_PREFIXES)


def check_message(message: str, required: tuple[str, ...], prefix_required: bool) -> list[str]:
    lines = message.splitlines()
    subject = lines[0] if lines else ""
    body = "\n".join(lines[1:])
    problems: list[str] = []

    if not subject.strip():
        return ["empty commit subject"]

    if is_exempt_subject(subject):
        return []

    if prefix_required and not SUBJECT_PATTERN.match(subject):
        problems.append(
            f"subject must start with TASK-ID: '<TASK-ID>: <summary>'; got '{subject[:60]}'"
        )

    if len(subject) > 72:
        problems.append(f"subject exceeds 72 chars ({len(subject)})")

    trailers = parse_trailers(body)
    for name in required:
        if name not in trailers:
            problems.append(f"missing trailer: {name}")
        elif not trailers[name].strip():
            problems.append(f"empty trailer value: {name}")
    return problems


def collect_messages_from_range(rev_range: str) -> list[tuple[str, str]]:
    out = subprocess.run(
        ["git", "log", "--format=%H%x00%B%x1e", rev_range],
        check=True,
        capture_output=True,
        text=True,
        cwd=ROOT,
    ).stdout
    items: list[tuple[str, str]] = []
    for chunk in out.split("\x1e"):
        chunk = chunk.strip("\n")
        if not chunk:
            continue
        sha, _, body = chunk.partition("\x00")
        items.append((sha, body))
    return items


def main() -> int:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--message-file", help="Path to a commit message file (commit-msg hook)")
    group.add_argument("--range", dest="rev_range", help="Git rev range (e.g. origin/dev..HEAD)")
    group.add_argument("--rev", help="Single commit sha to check")
    parser.add_argument(
        "--skip-merge",
        action="store_true",
        help="Ignore merge commits when checking a range",
    )
    args = parser.parse_args()

    # Allow CI/cron jobs to bypass with explicit opt-out (used by automated merge bots).
    if os.environ.get("PANTHEON_TRAILER_CHECK_DISABLED") == "1":
        return 0

    required, prefix_required = load_settings()

    targets: list[tuple[str, str]]
    if args.message_file:
        text = Path(args.message_file).read_text()
        # Strip comment lines (git includes them in the commit message file).
        text = "\n".join(l for l in text.splitlines() if not l.startswith("#"))
        targets = [("<staged>", text)]
    elif args.rev:
        body = subprocess.run(
            ["git", "log", "-1", "--format=%B", args.rev],
            check=True,
            capture_output=True,
            text=True,
            cwd=ROOT,
        ).stdout
        targets = [(args.rev, body)]
    else:
        targets = collect_messages_from_range(args.rev_range)

    exit_code = 0
    for sha, msg in targets:
        if args.skip_merge:
            parents = subprocess.run(
                ["git", "rev-list", "--parents", "-n", "1", sha],
                check=True,
                capture_output=True,
                text=True,
                cwd=ROOT,
            ).stdout.split()
            if len(parents) > 2:  # merge commit
                continue
        problems = check_message(msg, required, prefix_required)
        if problems:
            exit_code = 1
            print(f"\n[trailers] {sha}:")
            for p in problems:
                print(f"  - {p}")
    if exit_code:
        print(
            "\nFix: amend the commit message to include the required trailers. "
            "See docs/conventions/GIT_WORKFLOW.md §7."
        )
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
