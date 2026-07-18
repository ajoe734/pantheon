#!/usr/bin/env python3
"""Guard shared GitHub Actions controls in auto-worker GitHub CLI calls.

The orchestrator prepends its worker-only bin directory to PATH. Its ``gh``
wrapper invokes this module with an already-resolved real GitHub CLI binary,
which prevents this process from resolving the wrapper recursively.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
WORKER_GH_WRAPPER = (ROOT / ".orchestrator" / "bin" / "gh").resolve()
PROTECTED_REPOSITORIES = {
    ("ajoe734", "pantheon"),
    ("ajoe734", "execute-plans"),
}
BLOCKED_EXIT_CODE = 77


def _contains_command_pair(arguments: tuple[str, ...], command: str, actions: set[str]) -> bool:
    normalized = tuple(argument.lower() for argument in arguments)
    return any(
        normalized[index] == command and normalized[index + 1] in actions
        for index in range(len(normalized) - 1)
    )


def _api_path_parts(candidate: str) -> tuple[str, ...]:
    """Return normalized API path components for a CLI endpoint candidate."""

    decoded = unquote(candidate.strip())
    parsed = urlsplit(decoded)
    path = parsed.path if parsed.scheme or parsed.netloc else decoded.split("?", 1)[0].split("#", 1)[0]
    return tuple(part.lower() for part in path.split("/") if part)


def _protected_mutation_endpoint(candidate: str) -> bool:
    parts = _api_path_parts(candidate)
    for index, part in enumerate(parts):
        if part != "repos" or len(parts) < index + 7:
            continue
        owner, repository = parts[index + 1 : index + 3]
        if (owner, repository) not in PROTECTED_REPOSITORIES:
            continue
        action_parts = parts[index + 3 :]
        if (
            len(action_parts) == 4
            and action_parts[:2] == ("actions", "runs")
            and action_parts[3] in {"cancel", "force-cancel"}
        ):
            return True
        if (
            len(action_parts) == 4
            and action_parts[:2] == ("actions", "workflows")
            and action_parts[3] == "disable"
        ):
            return True
    return False


def blocked_reason(arguments: tuple[str, ...]) -> str | None:
    """Explain why a GitHub CLI invocation is forbidden, if it is forbidden."""

    if _contains_command_pair(arguments, "workflow", {"disable"}):
        return "auto workers may not disable GitHub Actions workflows"
    if _contains_command_pair(arguments, "run", {"cancel", "force-cancel"}):
        return "auto workers may not cancel GitHub Actions runs"

    if any(argument.lower() == "api" for argument in arguments):
        if any(_protected_mutation_endpoint(argument) for argument in arguments):
            return "auto workers may not mutate shared deploy workflow or run state through gh api"
    return None


def _parse_wrapper_arguments(arguments: list[str]) -> tuple[Path, tuple[str, ...]]:
    if len(arguments) < 2 or arguments[0] != "--gh-bin" or not arguments[1].strip():
        raise ValueError("usage: guard_shared_deploy_cli.py --gh-bin PATH -- [GH_ARGS ...]")
    gh_binary = Path(arguments[1]).expanduser().resolve()
    gh_arguments = arguments[2:]
    if gh_arguments[:1] == ["--"]:
        gh_arguments = gh_arguments[1:]
    return gh_binary, tuple(gh_arguments)


def main(arguments: list[str] | None = None) -> int:
    try:
        gh_binary, gh_arguments = _parse_wrapper_arguments(list(arguments if arguments is not None else sys.argv[1:]))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if gh_binary == WORKER_GH_WRAPPER:
        print("GitHub CLI worker guard refused a recursive gh binary.", file=sys.stderr)
        return 2

    reason = blocked_reason(gh_arguments)
    if reason is not None:
        print(f"GitHub CLI worker guard blocked this command: {reason}.", file=sys.stderr)
        return BLOCKED_EXIT_CODE

    os.execv(str(gh_binary), [str(gh_binary), *gh_arguments])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
