#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

THIS_DIR = Path(__file__).resolve().parent
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))

import release_hardening


BLOCKED_RUNTIME_PATTERNS = (
    re.compile(r"(^|/)ai-activity-log\.jsonl$"),
    re.compile(r"(^|/)ai-activity-log\.jsonl\.[0-9]+$"),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fail when staged paths include generated runtime artifacts that should never be committed.",
    )
    parser.add_argument("paths", nargs="*", help="Paths to check. If omitted, read newline-delimited paths from stdin.")
    parser.add_argument(
        "--diff-name-status",
        action="store_true",
        help="Treat input as git diff --name-status records instead of plain paths.",
    )
    parser.add_argument(
        "--allow-deleted-path",
        action="append",
        default=[],
        help="With --diff-name-status, allow a pure deletion for this exact path.",
    )
    return parser.parse_args()


def load_paths(args: argparse.Namespace) -> list[str]:
    entries = args.paths or [line for line in sys.stdin.read().splitlines() if line]
    if not args.diff_name_status:
        return [path for path in entries if path]

    allowed_deleted_paths = set(args.allow_deleted_path)
    paths: list[str] = []
    for entry in entries:
        fields = entry.split("\t")
        status, *changed_paths = fields
        if not status or not changed_paths:
            raise ValueError(f"invalid git diff --name-status record: {entry!r}")
        if status == "D" and len(changed_paths) == 1 and changed_paths[0] in allowed_deleted_paths:
            continue
        paths.extend(changed_paths)
    return paths


def main() -> int:
    args = parse_args()
    try:
        paths = load_paths(args)
    except ValueError as error:
        print(f"pre-commit: {error}", file=sys.stderr)
        return 2
    violations = [
        path
        for path in paths
        if release_hardening.is_generated_ephemeral(path)
        or any(pattern.search(path) for pattern in BLOCKED_RUNTIME_PATTERNS)
    ]
    if not violations:
        return 0

    print("pre-commit: refusing to commit generated runtime files:", file=sys.stderr)
    for path in violations:
        print(f"  - {path}", file=sys.stderr)
    print(file=sys.stderr)
    print("These paths are runtime output or generated mirrors and should stay out of git history.", file=sys.stderr)
    print("If a stray file was staged, run: git restore --staged <path>", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
