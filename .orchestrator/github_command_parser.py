#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class GitHubCommand:
    verb: str
    target: str | None
    raw: str
    args: tuple[str, ...] = ()


SUPPORTED_COMMANDS = {
    "approve",
    "deny",
    "retry",
    "resume",
    "recheck",
    "status",
    "dispatch",
    "needs-runtime",
    "contract-ready",
    "approve-engine",
}


def parse_command(comment_body: str) -> GitHubCommand | None:
    if not comment_body:
        return None
    first_line = comment_body.strip().splitlines()[0].strip()
    if not first_line.startswith("/"):
        return None
    parts = first_line[1:].split(None, 1)
    if not parts:
        return None
    verb = parts[0].strip().lower()
    if verb not in SUPPORTED_COMMANDS:
        return None
    args = tuple(parts[1].strip().split()) if len(parts) > 1 and parts[1].strip() else ()
    target = args[0] if args else None
    return GitHubCommand(verb=verb, target=target, raw=first_line, args=args)
