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
    return parser.parse_args()


def load_paths(args: argparse.Namespace) -> list[str]:
    if args.paths:
        return [path for path in args.paths if path]
    return [line.strip() for line in sys.stdin.read().splitlines() if line.strip()]


def main() -> int:
    args = parse_args()
    violations = [
        path
        for path in load_paths(args)
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
