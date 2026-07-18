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
PROTECTED_REPOSITORY_IDS = {"1201361718", "1230426594"}
REPOSITORY_PLACEHOLDERS = {"{owner}", "{repo}"}
BLOCKED_EXIT_CODE = 77


def _contains_ordered_command(arguments: tuple[str, ...], command: str, actions: set[str]) -> bool:
    """Return true when an action follows its command, allowing flags between."""

    normalized = tuple(argument.lower() for argument in arguments)
    for index, argument in enumerate(normalized):
        if argument == command and any(candidate in actions for candidate in normalized[index + 1 :]):
            return True
    return False


def _api_path_parts(candidate: str) -> tuple[str, ...]:
    """Return normalized API path components for a CLI endpoint candidate."""

    decoded = unquote(candidate.strip())
    parsed = urlsplit(decoded)
    path = parsed.path if parsed.scheme or parsed.netloc else decoded.split("?", 1)[0].split("#", 1)[0]
    return tuple(part.lower() for part in path.split("/") if part)


def _is_actions_mutation_path(action_parts: tuple[str, ...]) -> bool:
    if len(action_parts) != 4:
        return False
    resource = action_parts[:2]
    mutation = action_parts[3]
    if resource == ("actions", "runs"):
        return mutation in {"cancel", "force-cancel"}
    if resource == ("actions", "workflows"):
        return mutation == "disable"
    return False


def _protected_mutation_endpoint(candidate: str) -> bool:
    parts = _api_path_parts(candidate)
    for index, part in enumerate(parts):
        if part == "repositories" and len(parts) >= index + 6:
            if parts[index + 1] in PROTECTED_REPOSITORY_IDS and _is_actions_mutation_path(parts[index + 2 :]):
                return True
            continue
        if part != "repos" or len(parts) < index + 7:
            continue
        owner, repository = parts[index + 1 : index + 3]
        repo_is_protected = (owner, repository) in PROTECTED_REPOSITORIES
        repo_is_deferred = owner in REPOSITORY_PLACEHOLDERS or repository in REPOSITORY_PLACEHOLDERS
        if (repo_is_protected or repo_is_deferred) and _is_actions_mutation_path(parts[index + 3 :]):
            return True
    return False


def blocked_reason(arguments: tuple[str, ...]) -> str | None:
    """Explain why a GitHub CLI invocation is forbidden, if it is forbidden."""

    if _contains_ordered_command(arguments, "workflow", {"disable"}):
        return "auto workers may not disable GitHub Actions workflows"
    if _contains_ordered_command(arguments, "run", {"cancel", "force-cancel"}):
        return "auto workers may not cancel GitHub Actions runs"
    if _contains_ordered_command(arguments, "alias", {"set", "import"}):
        return "auto workers may not create GitHub CLI aliases that bypass command guards"

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
