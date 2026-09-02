#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

THIS_DIR = Path(__file__).resolve().parent
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))

from approval_queue import (
    consume_resume_override,
    create_approval,
    find_resume_override,
    validated_approval_binding,
)
from common import (
    ROOT,
    approval_tool_input_signature,
    load_config,
    load_json,
    load_status,
    normalize_agent_id,
    utc_now,
    write_activity_log,
    write_json,
)
from provider_permissions import CLAUDE_LOCAL_SETTINGS_PATH, _verified_claude_policy
from runtime_state import load_approval_state, load_runtime_state


SAFE_BASH_PATTERNS = [
    re.compile(r"^pwd$"),
    re.compile(r"^echo(\s|$)"),
    re.compile(r"^printf(\s|$)"),
    re.compile(r"^ls(\s|$)"),
    re.compile(r"^find(\s|$)"),
    re.compile(r"^grep(\s|$)"),
    re.compile(r"^rg(\s|$)"),
    re.compile(r"^cat(\s|$)"),
    re.compile(r"^sed(\s|$)"),
    re.compile(r"^head(\s|$)"),
    re.compile(r"^tail(\s|$)"),
    re.compile(r"^wc(\s|$)"),
    re.compile(r"^sort(\s|$)"),
    re.compile(r"^uniq(\s|$)"),
    re.compile(r"^awk(\s|$)"),
    re.compile(r"^jq(\s|$)"),
    re.compile(r"^ps(\s|$)"),
    re.compile(r"^pgrep(\s|$)"),
    re.compile(r"^which(\s|$)"),
    re.compile(r"^type(\s|$)"),
    re.compile(r"^date(\s|$)"),
    re.compile(r"^sleep(\s|$)"),
    re.compile(r"^git status(\s|$)"),
    re.compile(r"^git diff(\s|$)"),
    re.compile(r"^git show(\s|$)"),
    re.compile(r"^git log(\s|$)"),
    re.compile(r"^git branch(\s|$)"),
    re.compile(r"^git push(\s|$)"),
    re.compile(r"^git submodule status(\s|$)"),
    re.compile(r"^git -C .+ (status|diff|show|log|remote -v|submodule status)(\s|$)"),
    re.compile(r"^gh issue comment(\s|$)"),
    re.compile(r"^gh pr create(\s|$)"),
    re.compile(r"^git remote -v$"),
    re.compile(r"^git -C .+ (add|commit|push|remote set-url|submodule|rm)"),
    re.compile(r"^git rm(\s|$)"),
    re.compile(r"^python3 scripts/ai_status\.py(\s|$)"),
    re.compile(r"^python3 -m unittest(\s|$)"),
    re.compile(r"^cd .+ && python3 -m unittest(\s|$)"),
    re.compile(r"^python3 -m unittest discover(\s|$)"),
    re.compile(r"^cd .+ && python3 -m unittest discover(\s|$)"),
    re.compile(r"^python3 -m pytest(\s|$)"),
    re.compile(r"^cd .+ && python3 -m pytest(\s|$)"),
    re.compile(r"^pytest(\s|$)"),
    re.compile(r"^cd .+ && pytest(\s|$)"),
    re.compile(r"^apt(?:-get)? install(?:\s+-\S+)*\s+python3-pytest(?=\s|$)"),
    re.compile(r"^npm test(\s|$)"),
    re.compile(r"^cd .+ && npm test(\s|$)"),
    re.compile(r"^npm run test(\s|$)"),
    re.compile(r"^cd .+ && npm run test(\s|$)"),
    re.compile(r"^cargo test(\s|$)"),
    re.compile(r"^cd .+ && cargo test(\s|$)"),
    re.compile(r"^go test(\s|$)"),
    re.compile(r"^cd .+ && go test(\s|$)"),
    re.compile(r"^python3 -m py_compile(\s|$)"),
    re.compile(r"^cd .+ && python3 -m py_compile(\s|$)"),
    re.compile(r"^python3 (?:[A-Za-z0-9_./-]+/)?smoke_test\.py(?:\s|$)"),
    re.compile(r"^python3 (?:[A-Za-z0-9_./-]*/)?smoke_test[A-Za-z0-9_./-]*\.py(?:\s|$)"),
    re.compile(r"^cd .+ && python3 smoke_test\.py(?:\s|$)"),
    re.compile(r"^cd .+ && python3 (?:[A-Za-z0-9_./-]*/)?smoke_test[A-Za-z0-9_./-]*\.py(?:\s|$)"),
    re.compile(r"^python3 \.orchestrator/approval_queue\.py(\s|$)"),
    re.compile(r"^python3 \.orchestrator/doctor\.py(\s|$)"),
    re.compile(r"^python3 \.orchestrator/supervisor\.py(\s|$)"),
    re.compile(r"^nohup python3 \.orchestrator/supervisor\.py"),
    re.compile(r"^nohup python3 -m http\.server"),
    re.compile(r"^fuser \d+"),
    re.compile(r"^lsof -i:"),
    re.compile(r"^lsof -iTCP:"),
    re.compile(r"^kill \d+"),
    re.compile(r"^pkill(\s|$)"),
    # Dashboard server
    re.compile(r"^(?:(?:[A-Za-z_][A-Za-z0-9_]*=\S+)\s+)*bash\s+(?:\S+/)?scripts/launch-docs-site\.sh"),
    re.compile(r"^(?:(?:[A-Za-z_][A-Za-z0-9_]*=\S+)\s+)*bash\s+(?:\S+/)?scripts/run-dashboard\.sh"),
    re.compile(r"^(?:(?:[A-Za-z_][A-Za-z0-9_]*=\S+)\s+)*python3\s+(?:\S+/)?scripts/dashboard_server\.py"),
    re.compile(r"^nohup\s+(?:(?:[A-Za-z_][A-Za-z0-9_]*=\S+)\s+)*bash\s+(?:\S+/)?scripts/launch-docs-site\.sh"),
    re.compile(r"^tmux\s+(new-session|kill-session|attach|capture-pane|ls)"),
    # Misc dev tools
    re.compile(r"^node(\s|$)"),
    re.compile(r"^npx(\s|$)"),
    re.compile(r"^curl\s+-[fsS]"),  # safe read-only curl (no -o write)
    re.compile(r"^ss\s+"),
    re.compile(r"^netstat\s+"),
]


def load_broker_runtime_config() -> dict[str, Any]:
    """Load immutable broker policy with mutable state bound to status root.

    The broker executable/config belongs to ``PANTHEON_COMMAND_ROOT`` while
    runtime state belongs to the supervisor coordination root.  Keeping that
    distinction here prevents a promoted worker from either writing into an
    immutable/shared source checkout or treating its delivery worktree as
    governance authority.
    """

    config = load_config()
    raw_status_root = str(os.environ.get("PANTHEON_STATUS_ROOT") or "").strip()
    if not raw_status_root:
        return config

    status_root = Path(os.path.expanduser(raw_status_root))
    if not status_root.is_absolute():
        raise RuntimeError("PANTHEON_STATUS_ROOT must be absolute for permission broker state")
    status_root = status_root.resolve()
    if not status_root.is_dir():
        raise RuntimeError(
            f"PANTHEON_STATUS_ROOT does not exist for permission broker state: {status_root}"
        )

    rebound = dict(config)
    paths = dict(config.get("paths") or {})
    paths.update(
        {
            "status_file": str(status_root / "ai-status.json"),
            "activity_log": str(status_root / "ai-activity-log.jsonl"),
            "current_work": str(status_root / "current-work.md"),
            "dashboard": str(status_root / "docs-site" / "index.html"),
            "state_file": str(status_root / ".orchestrator" / "state.json"),
            "approval_queue": str(status_root / ".orchestrator" / "approval-queue.json"),
            "provider_capabilities": str(
                status_root / ".orchestrator" / "provider_capabilities.json"
            ),
            "claude_mcp_config": str(
                status_root / ".orchestrator" / "claude-approval-broker.mcp.json"
            ),
        }
    )
    rebound["paths"] = paths
    return rebound
DEFER_BASH_PATTERNS = [
    # Cloudflared tunnel: PSD-03 closes the standing worker grant here.
    # Publishing the dashboard needs an explicit operator decision, so an
    # unattended worker command is deferred to the approval queue instead
    # of auto-allowed.
    re.compile(r"^bash\s+(?:\S+/)?scripts/start_dashboard_tunnel\.sh"),
    re.compile(r"^cloudflared\s+tunnel"),
    re.compile(r"^git (add|commit|remote set-url|submodule)(\s|$)"),
    re.compile(r"^(curl|wget)(\s|$)"),
    re.compile(r"^(apt|apt-get)(\s|$)"),
    re.compile(r"^npm install(\s|$)"),
    re.compile(r"^python3\s+-m\s+pip install(\s|$)"),
    re.compile(r"^pip install(\s|$)"),
    re.compile(r"^pip3 install(\s|$)"),
    re.compile(r"^docker(\s|$)"),
]
DENY_BASH_PATTERNS = [
    re.compile(r"^git reset --hard"),
    re.compile(r"^git checkout --(\s|$)"),
    re.compile(r"^git clean(\s|$)"),
    re.compile(r"^git push(?:\s|$).*?(?:--force(?:-with-lease)?|-f|--mirror|--delete|--all|--tags|--prune|--atomic)(?:\s|$)"),
    re.compile(r"^sudo(\s|$)"),
    re.compile(r"^rm -rf /\*?$"),
    re.compile(r"^chmod 777(\s|$)"),
]

SAFE_TOOLS = {"Read", "Grep", "Glob", "LS", "Task", "TodoRead", "TodoWrite", "ReadNotebook", "ToolSearch"}
# Harness-internal orchestration tools that only poll/query in-session state
# (background task output, task metadata, cron schedules, monitor streams).
# They never touch the filesystem or network, so they are safe to auto-allow
# the same way SAFE_TOOLS is, but are kept in a distinct bucket/risk_class so
# the approval evidence trail still shows they are harness polling rather
# than repo reads. See OPS-APPROVAL-BROKER-RISK-CLASS-001: TaskOutput/Agent
# falling through to risk_class=unknown -> indefinite pending caused repeated
# multi-hour suspended_approval stalls for claude worker slots.
HARNESS_ORCHESTRATION_READ_TOOLS = {"TaskOutput", "TaskGet", "TaskList", "Monitor", "CronList"}
EDIT_TOOLS = {"Edit", "MultiEdit", "Write"}
NETWORK_TOOLS = {"WebFetch", "WebSearch"}
SAFE_AGENT_SUBAGENT_TYPES = {"explore", "review"}
# Substring markers checked against a normalized (lowercased, hyphen/underscore
# collapsed to spaces) subagent_type so variants like "code-review" or
# "code-reviewer" are recognized without needing an exact-match entry above.
SAFE_AGENT_SUBAGENT_TYPE_MARKERS = ("explore", "review")
SAFE_AGENT_MARKERS = (
    "verify",
    "find",
    "confirm",
    "locate",
    "report",
    "read",
    "grep",
    "search",
    "inspect",
    "check",
    "audit",
    "review",
    "explore",
    "list",
    "summarize",
)
UNSAFE_AGENT_MARKERS = (
    "edit",
    "write",
    "modify",
    "change",
    "create",
    "delete",
    "remove",
    "rename",
    "move",
    "commit",
    "push",
    "apply patch",
    "implement",
    "fix",
    "refactor",
    "generate",
    "add ",
    "update",
    "execute command",
    "execute commands",
    "execute shell",
    "execute bash",
    "execute script",
    "execute tests",
    "run ",
    "launch",
)
SAFE_AGENT_RUN_PATTERNS = (
    re.compile(r"\brun\s+`?git\s+status\b"),
    re.compile(r"\brun\s+`?git\s+log\b"),
    re.compile(r"\brun\s+`?git\s+diff\b"),
    re.compile(r"\brun\s+`?git\s+show\b"),
    re.compile(r"\brun\s+`?git\s+branch\b"),
    re.compile(r"\brun\s+`?git\s+remote\s+-v\b"),
    re.compile(r"\brun\s+`?rg\b"),
    re.compile(r"\brun\s+`?grep\b"),
    re.compile(r"\brun\s+`?find\b"),
    re.compile(r"\brun\s+`?ls\b"),
    re.compile(r"\brun\s+`?cat\b"),
    re.compile(r"\brun\s+`?sed\b"),
    re.compile(r"\brun\s+`?head\b"),
    re.compile(r"\brun\s+`?tail\b"),
    re.compile(r"\brun\s+`?wc\b"),
)

SAFE_PYTHON_ONE_LINER_MARKERS = (
    "print(",
    "with open(",
)
SAFE_PYTHON_JSON_LOAD_MARKERS = (
    "json.load",
    "json.loads",
)
UNSAFE_PYTHON_ONE_LINER_MARKERS = (
    ".write(",
    "write_text(",
    "write_bytes(",
    "append(",
    "unlink(",
    "rmdir(",
    "mkdir(",
    "rename(",
    "replace(",
    "chmod(",
    "chown(",
    "subprocess",
    "os.system",
    "requests.",
    "urllib.",
    "socket.",
)

STATUS_SYNC_BASH_PATTERNS = (
    re.compile(r"^(?:(?:[A-Za-z_][A-Za-z0-9_]*=\S+)\s+)*(?:bash\s+)?scripts/ai-status\.sh(?:\s|$)"),
    re.compile(r"^(?:(?:[A-Za-z_][A-Za-z0-9_]*=\S+)\s+)*python3\s+scripts/ai_status\.py(?:\s|$)"),
    re.compile(r"^cd\s+.+\s+&&\s+(?:(?:[A-Za-z_][A-Za-z0-9_]*=\S+)\s+)*python3\s+scripts/ai_status\.py(?:\s|$)"),
    re.compile(r"^cd\s+.+\s+&&\s+(?:(?:[A-Za-z_][A-Za-z0-9_]*=\S+)\s+)*(?:bash\s+)?scripts/ai-status\.sh(?:\s|$)"),
)

SAFE_PYTEST_VERIFY_PATTERNS = (
    re.compile(r"^python3 -m pytest(\s|$)"),
    re.compile(r"^pytest(\s|$)"),
    re.compile(r"^pip3? show pytest(\s|$)"),
)

FINALIZE_DISPATCH_REASON = "owned_finalize_dispatch"
FINALIZE_GIT_MESSAGE_FLAGS = {"-m", "--message"}
FINALIZE_GIT_ALLOWED_COMMIT_FLAGS = {"--amend", *FINALIZE_GIT_MESSAGE_FLAGS}


def _matches_workspace_script(path_token: str, relative_path: str) -> bool:
    candidate = Path(path_token)
    expected = ROOT / relative_path
    try:
        resolved = candidate.resolve(strict=False) if candidate.is_absolute() else (ROOT / candidate).resolve(strict=False)
    except OSError:
        return False
    return resolved == expected.resolve(strict=False)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Deterministic permission broker for Claude hooks and local approval broker flows.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    classify = subparsers.add_parser("classify", help="Classify a shell command as allow/defer/deny.")
    classify.add_argument("shell_command")

    evaluate = subparsers.add_parser("evaluate", help="Evaluate a Claude tool request.")
    evaluate.add_argument("tool_name")
    evaluate.add_argument("tool_input_json")

    hook = subparsers.add_parser("hook", help="Handle a Claude hook event.")
    hook.add_argument("event_name")

    log_hook = subparsers.add_parser("log-hook", help="Backward-compatible logging-only hook entrypoint.")
    log_hook.add_argument("event_name")

    remember = subparsers.add_parser("remember", help="Persist a suggested allow/deny rule into .claude/settings.local.json.")
    remember.add_argument("decision", choices=["allow", "deny", "ask"])
    remember.add_argument("rule")

    subparsers.add_parser("print-policy", help="Print the deterministic Claude permission policy as JSON.")
    return parser.parse_args()


def hook_payload() -> dict[str, Any]:
    raw = sys.stdin.read().strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw}


def classify_command(shell_command: str) -> str:
    if _is_safe_status_sync_command(shell_command):
        return "allow"
    if _is_safe_python_one_liner(shell_command):
        return "allow"
    if _is_safe_docker_command(shell_command):
        return "allow"
    if _is_safe_package_inventory_command(shell_command):
        return "allow"
    if _is_safe_pytest_install_command(shell_command):
        return "allow"
    if _is_safe_git_stage_flow_command(shell_command):
        return "allow"
    if _is_safe_git_commit_command(shell_command):
        return "allow"
    if _is_safe_git_push_command(shell_command):
        return "allow"
    if _is_safe_workspace_mkdir_command(shell_command):
        return "allow"
    for pattern in DENY_BASH_PATTERNS:
        if pattern.search(shell_command):
            return "deny"
    for pattern in SAFE_BASH_PATTERNS:
        if pattern.search(shell_command):
            return "allow"
    for pattern in DEFER_BASH_PATTERNS:
        if pattern.search(shell_command):
            return "defer"
    # Default: allow anything not explicitly denied or deferred.
    # settings.local.json has Bash(*) in the allow list; this aligns the broker.
    return "allow"


def _is_safe_python_one_liner(shell_command: str) -> bool:
    command = shell_command.strip()
    if not (command.startswith('python3 -c "') or command.startswith("python3 -c '")):
        return False
    try:
        parts = shlex.split(command)
    except ValueError:
        return False
    if len(parts) < 3 or parts[0] != "python3" or parts[1] != "-c":
        return False
    return _is_safe_python_inline_code(parts[2], require_import=False)


def _is_safe_python_inline_code(code: str, *, require_import: bool) -> bool:
    stripped = code.strip()
    if not stripped:
        return False
    if any(marker in stripped for marker in UNSAFE_PYTHON_ONE_LINER_MARKERS):
        return False
    chunks = [chunk.strip() for chunk in stripped.split(";") if chunk.strip()]
    if not chunks:
        return False
    saw_import = False
    for chunk in chunks:
        if chunk.startswith("import ") or chunk.startswith("from "):
            saw_import = True
            continue
        if chunk.startswith("print("):
            continue
        return False
    return saw_import if require_import else True


def _shell_command_segments(shell_command: str) -> list[str]:
    segments: list[str] = []
    current: list[str] = []
    quote: str | None = None
    escape = False
    for char in shell_command:
        if escape:
            current.append(char)
            escape = False
            continue
        if char == "\\":
            current.append(char)
            escape = True
            continue
        if quote:
            current.append(char)
            if char == quote:
                quote = None
            continue
        if char in {"'", '"'}:
            current.append(char)
            quote = char
            continue
        if char == ";":
            segment = "".join(current).strip()
            if segment:
                segments.append(segment)
            current = []
            continue
        current.append(char)
    tail = "".join(current).strip()
    if tail:
        segments.append(tail)
    return segments


def _shell_command_segments_with_and(shell_command: str) -> list[str]:
    segments: list[str] = []
    current: list[str] = []
    quote: str | None = None
    escape = False
    index = 0
    while index < len(shell_command):
        char = shell_command[index]
        if escape:
            current.append(char)
            escape = False
            index += 1
            continue
        if char == "\\":
            current.append(char)
            escape = True
            index += 1
            continue
        if quote:
            current.append(char)
            if char == quote:
                quote = None
            index += 1
            continue
        if char in {"'", '"'}:
            current.append(char)
            quote = char
            index += 1
            continue
        if char == ";" or shell_command.startswith("&&", index):
            segment = "".join(current).strip()
            if segment:
                segments.append(segment)
            current = []
            index += 2 if shell_command.startswith("&&", index) else 1
            continue
        current.append(char)
        index += 1
    tail = "".join(current).strip()
    if tail:
        segments.append(tail)
    return segments


def _primary_shell_fragment(segment: str) -> str:
    return segment.split("|", 1)[0].strip()


def _shell_tokens(fragment: str) -> list[str] | None:
    try:
        tokens = shlex.split(fragment)
    except ValueError:
        return None
    cleaned = [token for token in tokens if ">" not in token and "<" not in token]
    return cleaned or None


def _is_safe_docker_exec_probe(tokens: list[str]) -> bool:
    if len(tokens) < 6 or tokens[0] != "docker" or tokens[1] != "exec":
        return False
    index = 2
    while index < len(tokens) and tokens[index].startswith("-"):
        option = tokens[index]
        index += 1
        if option in {"-e", "--env", "-u", "--user", "-w", "--workdir"}:
            if index >= len(tokens):
                return False
            index += 1
        elif option in {"-i", "-t", "-it", "--interactive", "--tty"}:
            continue
        else:
            return False
    if index >= len(tokens):
        return False
    index += 1  # container name
    if len(tokens) <= index + 2:
        return False
    if tokens[index] not in {"python", "python3"} or tokens[index + 1] != "-c":
        return False
    return _is_safe_python_inline_code(tokens[index + 2], require_import=True)


def _is_safe_compose_option_value(token: str) -> bool:
    value = str(token or "")
    if value.startswith("-"):
        return False
    return bool(re.match(r"^[A-Za-z0-9_.:/@-]+$", value))


def _is_safe_compose_path_value(token: str) -> bool:
    value = str(token or "").strip()
    return bool(value and not value.startswith("-") and _looks_like_safe_repo_path(value))


def _is_safe_docker_compose_config(tokens: list[str]) -> bool:
    if len(tokens) < 3 or tokens[:2] != ["docker", "compose"]:
        return False
    index = 2
    saw_config = False
    path_options = {"-f", "--file", "--env-file", "--project-directory"}
    value_options = {"-p", "--project-name", "--profile", "--ansi", "--progress"}
    while index < len(tokens):
        token = tokens[index]
        if token == "config":
            saw_config = True
            index += 1
            break
        if token in path_options:
            if index + 1 >= len(tokens) or not _is_safe_compose_path_value(tokens[index + 1]):
                return False
            index += 2
            continue
        matched_path_option = next((option for option in path_options if token.startswith(f"{option}=")), None)
        if matched_path_option:
            value = token.split("=", 1)[1]
            if not _is_safe_compose_path_value(value):
                return False
            index += 1
            continue
        if token in value_options:
            if index + 1 >= len(tokens) or not _is_safe_compose_option_value(tokens[index + 1]):
                return False
            index += 2
            continue
        matched_value_option = next((option for option in value_options if token.startswith(f"{option}=")), None)
        if matched_value_option:
            value = token.split("=", 1)[1]
            if not _is_safe_compose_option_value(value):
                return False
            index += 1
            continue
        return False

    if not saw_config:
        return False

    config_flags = {
        "--quiet",
        "-q",
        "--services",
        "--volumes",
        "--profiles",
        "--images",
        "--hash",
        "--no-interpolate",
        "--no-normalize",
        "--no-consistency",
        "--resolve-image-digests",
    }
    while index < len(tokens):
        token = tokens[index]
        if token in config_flags:
            index += 1
            continue
        if token == "--format":
            if index + 1 >= len(tokens) or tokens[index + 1] not in {"json", "yaml"}:
                return False
            index += 2
            continue
        if token.startswith("--format="):
            if token.split("=", 1)[1] not in {"json", "yaml"}:
                return False
            index += 1
            continue
        return False
    return True


def _docker_compose_subcommand_index(tokens: list[str]) -> int | None:
    """Return the index of the docker-compose subcommand after skipping
    global flags (-p/--project-name, -f/--file, --env-file,
    --project-directory, --profile, --ansi, --progress), or None if a flag
    value fails validation or no subcommand token is present.

    "docker compose -p pantheon ps" and "docker compose ps" name the same
    read-only subcommand; only the position of "ps" differs. Without this,
    the common project-scoped form was misclassified as unsafe and forced
    an unresolvable manual approval for every worker that names its
    compose project explicitly (which every deployed L12 evidence suite
    and every isolated-compose task does).
    """

    if len(tokens) < 3 or tokens[:2] != ["docker", "compose"]:
        return None
    index = 2
    path_options = {"-f", "--file", "--env-file", "--project-directory"}
    value_options = {"-p", "--project-name", "--profile", "--ansi", "--progress"}
    while index < len(tokens):
        token = tokens[index]
        if token in path_options:
            if index + 1 >= len(tokens) or not _is_safe_compose_path_value(tokens[index + 1]):
                return None
            index += 2
            continue
        matched_path_option = next((option for option in path_options if token.startswith(f"{option}=")), None)
        if matched_path_option:
            value = token.split("=", 1)[1]
            if not _is_safe_compose_path_value(value):
                return None
            index += 1
            continue
        if token in value_options:
            if index + 1 >= len(tokens) or not _is_safe_compose_option_value(tokens[index + 1]):
                return None
            index += 2
            continue
        matched_value_option = next((option for option in value_options if token.startswith(f"{option}=")), None)
        if matched_value_option:
            value = token.split("=", 1)[1]
            if not _is_safe_compose_option_value(value):
                return None
            index += 1
            continue
        break
    return index if index < len(tokens) else None


def _is_safe_docker_segment(segment: str) -> bool:
    fragment = _primary_shell_fragment(segment)
    tokens = _shell_tokens(fragment)
    if not tokens or tokens[0] != "docker":
        return False
    if tokens[:2] in (["docker", "ps"], ["docker", "images"], ["docker", "inspect"], ["docker", "logs"]):
        return True
    if len(tokens) >= 3 and tokens[:3] in (["docker", "compose", "ps"], ["docker", "compose", "images"]):
        return True
    subcommand_index = _docker_compose_subcommand_index(tokens)
    if subcommand_index is not None and tokens[subcommand_index] in ("ps", "images"):
        return True
    if _is_safe_docker_compose_config(tokens):
        return True
    return _is_safe_docker_exec_probe(tokens)


def _is_safe_echo_segment(segment: str) -> bool:
    tokens = _shell_tokens(_primary_shell_fragment(segment))
    return bool(tokens and tokens[0] == "echo")


def _is_safe_docker_command(shell_command: str) -> bool:
    segments = _shell_command_segments_with_and(shell_command.strip())
    if not segments:
        return False
    return all(_is_safe_docker_segment(segment) or _is_safe_echo_segment(segment) for segment in segments)


def _is_safe_package_inventory_segment(segment: str) -> bool:
    fragment = _primary_shell_fragment(segment)
    tokens = _shell_tokens(fragment)
    if not tokens:
        return False
    if tokens[:2] == ["apt", "list"] and "--installed" in tokens:
        return True
    if tokens[0] == "find":
        allowed_roots = {"/usr", "/usr/bin", "/usr/local/bin", "/usr/local/lib", "/usr/lib"}
        index = 1
        roots: list[str] = []
        while index < len(tokens) and tokens[index].startswith("/"):
            roots.append(tokens[index])
            index += 1
        if not roots or not set(roots).issubset(allowed_roots):
            return False
        tail = tokens[index:]
        return tail in (
            ["-name", "pip*"],
            ["-name", "pip", "-type", "d"],
        )
    if tokens[0] in {"which", "type"} and all("pip" in token for token in tokens[1:]):
        return True
    return False


def _is_safe_package_inventory_command(shell_command: str) -> bool:
    segments = _shell_command_segments(shell_command.strip())
    if not segments:
        return False
    return all(_is_safe_package_inventory_segment(segment) for segment in segments)


def _is_safe_status_sync_command(shell_command: str) -> bool:
    command = shell_command.strip()
    try:
        parts = shlex.split(command)
    except ValueError:
        return any(pattern.search(command) for pattern in STATUS_SYNC_BASH_PATTERNS)
    if "&&" in parts:
        try:
            amp_index = parts.index("&&")
        except ValueError:
            amp_index = -1
        if amp_index == 2 and parts[0] == "cd":
            cd_target = parts[1]
            if not _paths_within_workspace([Path(cd_target)]):
                return False
            parts = parts[amp_index + 1 :]
    index = 0
    while index < len(parts) and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=.*$", parts[index]):
        index += 1
    if index >= len(parts):
        return False
    remaining = parts[index:]
    if len(remaining) >= 2 and remaining[0] == "python3" and (
        remaining[1] == "scripts/ai_status.py" or _matches_workspace_script(remaining[1], "scripts/ai_status.py")
    ):
        return True
    if len(remaining) >= 2 and remaining[0] == "bash" and (
        remaining[1] == "scripts/ai-status.sh" or _matches_workspace_script(remaining[1], "scripts/ai-status.sh")
    ):
        return True
    if remaining[0] == "scripts/ai-status.sh" or _matches_workspace_script(remaining[0], "scripts/ai-status.sh"):
        return True
    return any(pattern.search(command) for pattern in STATUS_SYNC_BASH_PATTERNS)


def _is_safe_workspace_mkdir_command(shell_command: str) -> bool:
    command = shell_command.strip()
    if not command.startswith("mkdir -p "):
        return False
    raw_paths = [item for item in command[len("mkdir -p ") :].split() if item]
    if not raw_paths:
        return False
    return _paths_within_workspace([Path(item) for item in raw_paths])


SAFE_TEST_DEPENDENCY_NAMES = {"pytest", "fastapi", "httpx", "pydantic", "anyio"}


def _is_safe_test_dependency_spec(token: str) -> bool:
    match = re.match(r"^([A-Za-z0-9_.-]+)(?:[=<>!~].+)?$", token)
    if not match:
        return False
    return match.group(1) in SAFE_TEST_DEPENDENCY_NAMES


def _split_safe_verification_segments(shell_command: str) -> list[str]:
    segments: list[str] = []
    current: list[str] = []
    quote: str | None = None
    index = 0
    while index < len(shell_command):
        char = shell_command[index]
        if quote:
            current.append(char)
            if char == quote:
                quote = None
            index += 1
            continue
        if char in {"'", '"'}:
            quote = char
            current.append(char)
            index += 1
            continue
        if shell_command.startswith("&&", index):
            segment = "".join(current).strip()
            if segment:
                segments.append(segment)
            current = []
            index += 2
            continue
        if char == ";":
            segment = "".join(current).strip()
            if segment:
                segments.append(segment)
            current = []
            index += 1
            continue
        current.append(char)
        index += 1

    tail = "".join(current).strip()
    if tail:
        segments.append(tail)
    return segments


def _is_shell_redirection_token(token: str) -> bool:
    return bool(re.match(r"^\d?(?:>>?|<<?|>&)\S*$", token))


def _is_safe_stderr_merge_token(token: str) -> bool:
    return str(token or "").strip() == "2>&1"


def _is_safe_pytest_install_command(shell_command: str) -> bool:
    command = shell_command.strip()
    if not command:
        return False

    segments = _split_safe_verification_segments(command)
    if not segments:
        return False

    install_fragment = segments[0].split("|", 1)[0].strip()
    try:
        tokens = shlex.split(install_fragment)
    except ValueError:
        return False

    package_tokens: list[str]
    if tokens[:4] == ["python3", "-m", "pip", "install"]:
        package_tokens = tokens[4:]
    elif tokens[:2] in (["pip", "install"], ["pip3", "install"]):
        package_tokens = tokens[2:]
    else:
        return False

    package_specs = [
        token
        for token in package_tokens
        if not token.startswith("-") and not _is_shell_redirection_token(token)
    ]
    if not package_specs or not all(_is_safe_test_dependency_spec(token) for token in package_specs):
        return False

    for remainder in segments[1:]:
        remainder = remainder.strip()
        if not remainder:
            continue
        if any(pattern.search(remainder) for pattern in SAFE_PYTEST_VERIFY_PATTERNS):
            continue
        return False

    return True


def _collect_paths(tool_input: dict[str, Any]) -> list[Path]:
    candidates: list[Path] = []
    for key, value in tool_input.items():
        if key not in {"path", "file_path", "old_path", "new_path", "paths", "files"}:
            continue
        if isinstance(value, str):
            candidates.append(Path(value))
        elif isinstance(value, list):
            candidates.extend(Path(item) for item in value if isinstance(item, str))
    return candidates


def _active_lease_workspace_root(config: dict[str, Any] | None = None) -> Path | None:
    """Return this worker's exact active isolated-worktree lease, if any.

    Workspace environment variables are candidate-visible launch context, not
    authority on their own.  Bind both variables to the central runtime worker
    record and its current canonical task generation before extending the
    broker's configured write roots.
    """

    run_id = str(os.environ.get("ORCH_RUN_ID") or "").strip()
    task_id = str(os.environ.get("ORCH_TASK_ID") or "").strip()
    agent_id = normalize_agent_id(os.environ.get("ORCH_AGENT_ID"))
    raw_worktree_root = str(os.environ.get("PANTHEON_WORKTREE_ROOT") or "").strip()
    raw_workspace_path = str(os.environ.get("ORCH_WORKSPACE_PATH") or "").strip()
    if not all((run_id, task_id, agent_id, raw_worktree_root, raw_workspace_path)):
        return None

    env_roots: list[Path] = []
    for raw_path in (raw_worktree_root, raw_workspace_path):
        candidate = Path(os.path.expanduser(raw_path))
        if not candidate.is_absolute():
            return None
        env_roots.append(candidate.resolve())
    workspace_root = env_roots[0]
    if env_roots[1] != workspace_root:
        return None

    try:
        runtime_config = config if config is not None else load_broker_runtime_config()
        runtime_state = load_runtime_state(runtime_config)
        status_state = load_status(runtime_config)
    except Exception:
        return None
    workers = runtime_state.get("workers")
    if not isinstance(workers, dict):
        return None
    worker = workers.get(run_id)
    if not isinstance(worker, dict):
        return None
    if str(worker.get("run_id") or "").strip() != run_id:
        return None
    if str(worker.get("status") or "").strip() != "running":
        return None
    if str(worker.get("task_id") or "").strip() != task_id:
        return None
    if normalize_agent_id(worker.get("agent_id")) != agent_id:
        return None
    if str(worker.get("workspace_mode") or "").strip() != "isolated_worktree":
        return None

    tasks = status_state.get("tasks")
    if not isinstance(tasks, list):
        return None
    task = next(
        (item for item in tasks if str(item.get("id") or "").strip() == task_id),
        None,
    )
    if not isinstance(task, dict):
        return None
    if normalize_agent_id(task.get("owner")) != agent_id:
        return None
    raw_worker_generation = worker.get("task_generation")
    raw_task_generation = task.get("generation")
    if isinstance(raw_worker_generation, bool) or isinstance(raw_task_generation, bool):
        return None
    try:
        worker_generation = int(raw_worker_generation)
        canonical_generation = int(raw_task_generation)
    except (TypeError, ValueError):
        return None
    if (
        worker_generation < 1
        or canonical_generation < 1
        or worker_generation != canonical_generation
    ):
        return None

    raw_expiry = str(worker.get("lease_expires_at") or "").strip()
    try:
        expires_at = datetime.fromisoformat(raw_expiry.replace("Z", "+00:00"))
    except ValueError:
        return None
    if expires_at.tzinfo is None or datetime.now(timezone.utc) >= expires_at:
        return None

    raw_worker_workspace = str(worker.get("workspace_path") or "").strip()
    if not raw_worker_workspace:
        return None
    worker_workspace = Path(os.path.expanduser(raw_worker_workspace))
    if not worker_workspace.is_absolute() or worker_workspace.resolve() != workspace_root:
        return None
    return workspace_root


def _allowed_workspace_roots(config: dict[str, Any] | None = None) -> list[Path]:
    roots = [ROOT, ROOT.parent / "pantheon"]
    configured = ((config or {}).get("permission_broker", {}) or {}).get("allowed_workspace_roots", [])
    if isinstance(configured, list):
        for item in configured:
            if not isinstance(item, str) or not item.strip():
                continue
            candidate = Path(item).expanduser()
            if not candidate.is_absolute():
                candidate = ROOT / candidate
            roots.append(candidate.resolve())
    lease_root = _active_lease_workspace_root(config)
    if lease_root is not None:
        roots.append(lease_root)
    return list(dict.fromkeys(roots))


def _paths_within_workspace(paths: list[Path], config: dict[str, Any] | None = None) -> bool:
    if not paths:
        return True
    allowed_roots = _allowed_workspace_roots(config)
    for path in paths:
        resolved = path.expanduser()
        resolved = resolved if resolved.is_absolute() else ROOT / resolved
        resolved = resolved.resolve()
        if not any(
            _is_relative_to(resolved, root) for root in allowed_roots
        ):
            return False
    return True


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _split_shell_segments(shell_command: str) -> list[list[str]] | None:
    try:
        tokens = shlex.split(shell_command)
    except ValueError:
        return None
    if not tokens:
        return None
    segments: list[list[str]] = []
    current: list[str] = []
    for token in tokens:
        if token in {"||", ";", "|"}:
            return None
        if token == "&&":
            if not current:
                return None
            segments.append(current)
            current = []
            continue
        current.append(token)
    if not current:
        return None
    segments.append(current)
    return segments


def _looks_like_safe_repo_path(token: str) -> bool:
    value = str(token or "").strip()
    if not value or value in {".", ".."}:
        return False
    if any(char in value for char in "*?[]{}"):
        return False
    path = Path(value)
    if any(part == ".." for part in path.parts):
        return False
    return _paths_within_workspace([path])


def _strip_workspace_cd_segment(segments: list[list[str]]) -> list[list[str]] | None:
    if not segments:
        return None
    if segments[0][0] != "cd":
        return segments
    if len(segments[0]) != 2:
        return None
    if not _paths_within_workspace([Path(segments[0][1])]):
        return None
    return segments[1:]


def _is_safe_git_read_segment(segment: list[str]) -> bool:
    if len(segment) < 2 or segment[0] != "git":
        return False
    return segment[1] in {"status", "diff", "show", "log", "branch"}


def _is_safe_git_stage_flow_command(shell_command: str) -> bool:
    segments = _split_shell_segments(shell_command)
    if not segments:
        return False

    normalized = _strip_workspace_cd_segment(segments)
    if not normalized:
        return False
    if not _is_safe_finalize_git_add(normalized[0]):
        return False
    # Allow trailing git commit segment after git add (e.g. git add ... && git commit -m "...")
    remaining = normalized[1:]
    if remaining and _is_safe_finalize_git_commit(remaining[-1]):
        remaining = remaining[:-1]
    return all(_is_safe_git_read_segment(segment) for segment in remaining)


def _is_safe_git_commit_command(shell_command: str) -> bool:
    segments = _split_shell_segments(shell_command)
    if not segments:
        return False
    normalized = _strip_workspace_cd_segment(segments)
    if not normalized or len(normalized) != 1:
        return False
    return _is_safe_finalize_git_commit(normalized[0])


def _is_safe_git_push_command(shell_command: str) -> bool:
    segments = _split_shell_segments(shell_command)
    if not segments:
        return False
    normalized = _strip_workspace_cd_segment(segments)
    if not normalized or len(normalized) != 1:
        return False
    return _is_safe_finalize_git_push(normalized[0])


def _is_safe_finalize_git_add(segment: list[str]) -> bool:
    if len(segment) < 3 or segment[:2] != ["git", "add"]:
        return False
    args = segment[2:]
    if any(arg.startswith("-") for arg in args):
        return False
    return all(_looks_like_safe_repo_path(arg) for arg in args)


def _is_safe_finalize_git_commit(segment: list[str]) -> bool:
    if len(segment) < 4 or segment[:2] != ["git", "commit"]:
        return False
    args = segment[2:]
    saw_message = False
    index = 0
    while index < len(args):
        token = args[index]
        if _is_safe_stderr_merge_token(token):
            index += 1
            continue
        if token not in FINALIZE_GIT_ALLOWED_COMMIT_FLAGS:
            return False
        if token in FINALIZE_GIT_MESSAGE_FLAGS:
            index += 1
            if index >= len(args) or not str(args[index]).strip():
                return False
            saw_message = True
        index += 1
    return saw_message


def _looks_like_safe_push_ref(token: str) -> bool:
    value = str(token or "").strip()
    if not value or value.startswith("-") or value.startswith(":") or ":" in value:
        return False
    return bool(re.match(r"^[A-Za-z0-9._/@-]+$", value))


def _is_safe_finalize_git_push(segment: list[str]) -> bool:
    if len(segment) < 2 or segment[:2] != ["git", "push"]:
        return False
    args = segment[2:]
    if len(args) > 2:
        return False
    return all(_looks_like_safe_push_ref(arg) for arg in args)


def _load_finalize_dispatch_context(config: dict[str, Any]) -> dict[str, Any] | None:
    run_id = str(os.environ.get("ORCH_RUN_ID") or "").strip()
    task_id = str(os.environ.get("ORCH_TASK_ID") or "").strip()
    agent_id = normalize_agent_id(os.environ.get("ORCH_AGENT_ID"))
    if not run_id or not task_id or not agent_id:
        return None

    try:
        runtime_state = load_runtime_state(config)
        status_state = load_status(config)
    except Exception:
        return None

    worker = runtime_state.get("workers", {}).get(run_id)
    if not isinstance(worker, dict):
        return None
    if str(worker.get("task_id") or "").strip() != task_id:
        return None

    request_snapshot = worker.get("request_snapshot") or {}
    if str(request_snapshot.get("reason") or "").strip() != FINALIZE_DISPATCH_REASON:
        return None

    tasks = status_state.get("tasks", [])
    if not isinstance(tasks, list):
        return None
    task = next((item for item in tasks if str(item.get("id") or "").strip() == task_id), None)
    if not isinstance(task, dict):
        return None
    if str(task.get("status") or "").strip() != "review_approved":
        return None
    if normalize_agent_id(task.get("owner")) != agent_id:
        return None

    return {
        "run_id": run_id,
        "task_id": task_id,
        "agent_id": agent_id,
        "worker": worker,
        "task": task,
    }


def _finalize_git_decision(shell_command: str, config: dict[str, Any]) -> dict[str, str] | None:
    context = _load_finalize_dispatch_context(config)
    if context is None:
        return None

    segments = _split_shell_segments(shell_command)
    if not segments or len(segments) > 3:
        return None

    index = 0
    saw_commit = False
    saw_push = False

    if _is_safe_finalize_git_add(segments[index]):
        index += 1
        if index >= len(segments):
            return None

    if _is_safe_finalize_git_commit(segments[index]):
        saw_commit = True
        index += 1
        if index < len(segments):
            if not _is_safe_finalize_git_push(segments[index]):
                return None
            saw_push = True
            index += 1
    elif _is_safe_finalize_git_push(segments[index]):
        saw_push = True
        index += 1
    else:
        return None

    if index != len(segments):
        return None
    if not saw_commit and not saw_push:
        return None

    verbs: list[str] = []
    if saw_commit:
        verbs.append("git commit")
    if saw_push:
        verbs.append("git push")
    verb_phrase = " and ".join(verbs)
    task_id = context["task_id"]
    return {
        "decision": "allow",
        "reason": f"Auto-allowed safe finalize {verb_phrase} for {task_id} during {FINALIZE_DISPATCH_REASON}.",
        "risk_class": "repo_finalize_git",
    }


def _worker_status_runtime_decision(shell_command: str) -> dict[str, str] | None:
    """Reject governed status writes through a stale worker checkout.

    Auto workers receive one installed command runtime. A relative
    ``scripts/ai_status.py`` from their task or central status checkout can be
    older than the live supervisor and can publish an incompatible legacy
    rotation. Non-worker developer commands are intentionally unaffected.
    """

    if not (
        str(os.environ.get("ORCH_RUN_ID") or "").strip()
        or str(os.environ.get("ORCH_TASK_ID") or "").strip()
    ):
        return None
    command_root_raw = str(os.environ.get("PANTHEON_COMMAND_ROOT") or "").strip()
    command_root = Path(command_root_raw).expanduser().resolve() if command_root_raw else None
    current_dir: str | None = None
    saw_status_command = False
    for segment_raw in _shell_command_segments_with_and(shell_command):
        try:
            tokens = shlex.split(segment_raw)
        except ValueError:
            continue
        if not tokens:
            continue
        if tokens[0] == "cd" and len(tokens) == 2:
            current_dir = tokens[1]
            continue
        index = 0
        while index < len(tokens) and re.match(
            r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[index]
        ):
            index += 1
        if index < len(tokens) and tokens[index] == "timeout":
            index += 1
            while index < len(tokens) and tokens[index].startswith("-"):
                index += 1
            if index < len(tokens):
                index += 1
        if index >= len(tokens):
            continue
        if tokens[index] in {"python", "python3", "bash", "sh"}:
            index += 1
        if index >= len(tokens):
            continue
        script = tokens[index]
        normalized = script.replace("\\", "/")
        if not normalized.endswith(
            ("scripts/ai_status.py", "scripts/ai-status.sh")
        ):
            continue
        saw_status_command = True
        if normalized.startswith(
            ("$PANTHEON_COMMAND_ROOT/", "${PANTHEON_COMMAND_ROOT}/")
        ):
            continue
        script_path = Path(script).expanduser()
        if script_path.is_absolute() and command_root is not None:
            try:
                script_path.resolve().relative_to(command_root)
                continue
            except ValueError:
                pass
        if not script_path.is_absolute() and command_root is not None and current_dir:
            if current_dir in ("$PANTHEON_COMMAND_ROOT", "${PANTHEON_COMMAND_ROOT}"):
                continue
            try:
                if Path(current_dir).expanduser().resolve() == command_root:
                    continue
            except OSError:
                pass
        return {
            "decision": "deny",
            "reason": (
                "Auto-worker status commands must run through "
                "$PANTHEON_COMMAND_ROOT; the requested script resolves through "
                "a stale checkout."
            ),
            "risk_class": "stale_status_command_runtime",
        }
    if saw_status_command and command_root is None:
        return {
            "decision": "deny",
            "reason": "Auto-worker status command runtime is not pinned.",
            "risk_class": "stale_status_command_runtime",
        }
    return None


def evaluate_tool_request(tool_name: str, tool_input: dict[str, Any] | None, config: dict[str, Any]) -> dict[str, Any]:
    tool_input = tool_input or {}
    decision = "defer"
    reason = f"Deferred by default for {tool_name}."
    risk_class = "unknown"
    suggested_rule = None

    if tool_name in SAFE_TOOLS:
        decision = "allow"
        reason = f"{tool_name} is read-only."
        risk_class = "safe_read"
    elif tool_name in HARNESS_ORCHESTRATION_READ_TOOLS:
        decision = "allow"
        reason = f"{tool_name} is a read-only harness orchestration/polling tool."
        risk_class = "harness_orchestration_read"
    elif tool_name == "Agent":
        agent_decision = _evaluate_agent_request(tool_input)
        if agent_decision is not None:
            decision = agent_decision["decision"]
            reason = agent_decision["reason"]
            risk_class = agent_decision["risk_class"]
    elif tool_name in EDIT_TOOLS:
        if _paths_within_workspace(_collect_paths(tool_input), config):
            decision = "allow"
            reason = f"{tool_name} stays within the repository workspace."
            risk_class = "repo_write"
        else:
            decision = "deny"
            reason = f"{tool_name} targets a path outside {ROOT}."
            risk_class = "out_of_workspace"
    elif tool_name == "Bash":
        shell_command = tool_input.get("command") or tool_input.get("cmd") or tool_input.get("raw_command") or ""
        status_runtime_decision = _worker_status_runtime_decision(str(shell_command))
        finalize_decision = _finalize_git_decision(str(shell_command), config)
        if status_runtime_decision is not None:
            decision = status_runtime_decision["decision"]
            risk_class = status_runtime_decision["risk_class"]
            reason = status_runtime_decision["reason"]
        elif finalize_decision is not None:
            decision = finalize_decision["decision"]
            risk_class = finalize_decision["risk_class"]
            reason = finalize_decision["reason"]
        else:
            decision = classify_command(str(shell_command))
            risk_class = {
                "allow": "safe_bash",
                "deny": "destructive_bash",
                "defer": "needs_review",
            }[decision]
            reason = f"Bash command classified as {decision}: {shell_command}"
        suggested_rule = f"Bash({shell_command})" if shell_command else None
    elif tool_name in NETWORK_TOOLS:
        decision = "defer"
        reason = f"{tool_name} requires network approval."
        risk_class = "network"

    provider_id = _approval_provider(config)
    return {
        "decision": decision,
        "reason": reason,
        "risk_class": risk_class,
        "suggested_rule": suggested_rule,
        "tool_name": tool_name,
        "tool_input": tool_input,
        "evaluated_at": utc_now(),
        "policy_default_mode": (
            config.get("providers", {}).get(provider_id, {}).get("approval", {}).get("rule_default_mode", "acceptEdits")
        ),
    }


def _marker_pattern(marker: str) -> re.Pattern[str]:
    normalized = re.escape(marker.strip()).replace(r"\ ", r"\s+")
    return re.compile(rf"\b{normalized}\b")


def _contains_marker(text: str, markers: tuple[str, ...]) -> bool:
    for marker in markers:
        if _marker_pattern(marker).search(text):
            return True
    return False


def _unsafe_agent_marker_is_negated(text: str, marker_start: int) -> bool:
    prefix = text[max(0, marker_start - 48) : marker_start]
    return bool(
        re.search(
            (
                r"(?:^|[\s.;:!?(\[])"
                r"(?:do\s+not|don't|dont|never|must\s+not|should\s+not|cannot|can't|no\s+need\s+to|without|avoid)"
                r"\s+(?:\w+\s+){0,3}$"
            ),
            prefix,
        )
    )


def _unsafe_agent_marker_is_read_only_context(text: str, marker: str, start: int, end: int) -> bool:
    prefix = text[max(0, start - 40) : start]
    suffix = text[end : end + 40]
    previous_word = re.search(r"\b([a-z]+)\W*$", prefix)
    if marker == "change":
        if previous_word and previous_word.group(1) in {
            "approved",
            "current",
            "existing",
            "implemented",
            "merged",
            "reviewed",
        }:
            return True
        return bool(re.match(r"\s+(?:already\s+)?(?:in|under|being|is|was|has)\b", suffix))
    if marker == "commit":
        if previous_word and previous_word.group(1) in {
            "base",
            "current",
            "exact",
            "head",
            "merged",
            "relevant",
            "reviewed",
            "target",
        }:
            return True
        return bool(re.match(r"[\s,]+(?:under|already|being|is|was|has|on|in)\b", suffix))
    return False


def _contains_unsafe_agent_action_marker(text: str, markers: tuple[str, ...]) -> bool:
    for marker in markers:
        normalized = marker.strip()
        if not normalized:
            continue
        for match in _marker_pattern(normalized).finditer(text):
            if _unsafe_agent_marker_is_negated(text, match.start()):
                continue
            if _unsafe_agent_marker_is_read_only_context(text, normalized, match.start(), match.end()):
                continue
            return True
    return False


def _contains_unsafe_agent_push_marker(text: str) -> bool:
    for match in re.finditer(r"\bpush\b", text):
        if _unsafe_agent_marker_is_negated(text, match.start()):
            continue
        after = text[match.end() : match.end() + 16]
        if re.match(r"[_\s-]*(status|state)\b", after):
            continue
        return True
    return False


def _contains_unsafe_agent_marker(text: str) -> bool:
    non_run_markers = tuple(marker for marker in UNSAFE_AGENT_MARKERS if marker.strip() not in {"push", "run"})
    if _contains_unsafe_agent_action_marker(text, non_run_markers):
        return True
    if _contains_unsafe_agent_push_marker(text):
        return True
    unnegated_run_markers = [
        match
        for match in re.finditer(r"\brun\b", text)
        if not _unsafe_agent_marker_is_negated(text, match.start())
    ]
    if not unnegated_run_markers:
        return False
    return not any(pattern.search(text) for pattern in SAFE_AGENT_RUN_PATTERNS)


def _normalize_subagent_type(value: str) -> str:
    return re.sub(r"[-_]+", " ", value.strip().lower())


def _is_safe_agent_subagent_type(subagent_type: str) -> bool:
    if subagent_type in SAFE_AGENT_SUBAGENT_TYPES:
        return True
    normalized = _normalize_subagent_type(subagent_type)
    return any(marker in normalized for marker in SAFE_AGENT_SUBAGENT_TYPE_MARKERS)


def _evaluate_agent_request(tool_input: dict[str, Any]) -> dict[str, str] | None:
    description = str(tool_input.get("description") or "")
    prompt = str(tool_input.get("prompt") or "")
    subagent_type = str(tool_input.get("subagent_type") or "").strip().lower()
    combined = f"{description}\n{prompt}".strip().lower()
    if not combined:
        return None
    if _contains_unsafe_agent_marker(combined):
        return None
    if _is_safe_agent_subagent_type(subagent_type) or _contains_marker(combined, SAFE_AGENT_MARKERS):
        return {
            "decision": "allow",
            "reason": "Agent request is scoped to read-only repo exploration/review.",
            "risk_class": "safe_read",
        }
    return None


def _approval_provider(config: dict[str, Any]) -> str:
    provider_id = str(os.environ.get("ORCH_PROVIDER") or "claude").strip().lower() or "claude"
    provider = (config.get("providers", {}) or {}).get(provider_id, {}) or {}
    delivery_mode = str(provider.get("delivery_mode") or "").strip()
    if delivery_mode and delivery_mode != "claude_cli":
        return "claude"
    if provider or provider_id.startswith("claude"):
        return provider_id
    return "claude"


def remember_rule(config: dict[str, Any], *, decision: str, rule: str) -> dict[str, Any]:
    settings = load_json(CLAUDE_LOCAL_SETTINGS_PATH, default={}) or {}
    permissions = settings.get("permissions", {})
    bucket = permissions.get(decision, []) or []
    if rule not in bucket:
        bucket.append(rule)
    permissions[decision] = bucket
    settings["permissions"] = permissions
    write_json(CLAUDE_LOCAL_SETTINGS_PATH, settings)
    write_activity_log(
        config,
        {
            "type": "permission_rule_remembered",
            "provider": _approval_provider(config),
            "message": f"Remembered Claude rule in {decision}: {rule}",
            "decision": decision,
            "rule": rule,
        },
    )
    return settings


def emit_hook_response(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False))


def log_event(config: dict[str, Any], event_name: str, payload: dict[str, Any]) -> None:
    message = payload.get("tool_name") or payload.get("toolName") or payload.get("raw") or event_name
    provider_id = _approval_provider(config)
    write_activity_log(
        config,
        {
            "type": "permission_hook",
            "provider": provider_id,
            "message": f"{event_name}: {message}",
            "hook_event": event_name,
            "hook_payload": payload,
            "ts_local": utc_now(),
        },
    )


def _approval_context(payload: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    provider_id = _approval_provider(config)
    return {
        "provider": provider_id,
        "task_id": os.environ.get("ORCH_TASK_ID") or payload.get("task_id") or payload.get("taskId"),
        "task_generation": os.environ.get("ORCH_TASK_GENERATION"),
        "worker_run_id": os.environ.get("ORCH_RUN_ID"),
        "agent_id": os.environ.get("ORCH_AGENT_ID"),
        "session_id": payload.get("session_id") or payload.get("sessionId") or os.environ.get("ORCH_SESSION_ID"),
        "tool_use_id": payload.get("tool_use_id") or payload.get("toolUseId"),
        "expires_at": None,
    }


def _decision_response(event_name: str, permission_decision: str, reason: str) -> dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": event_name,
            "permissionDecision": permission_decision,
            "permissionDecisionReason": reason,
        }
    }


def _permission_request_response(
    behavior: str,
    *,
    message: str | None = None,
    updated_input: dict[str, Any] | None = None,
    updated_permissions: list[dict[str, Any]] | None = None,
    interrupt: bool | None = None,
) -> dict[str, Any]:
    decision: dict[str, Any] = {"behavior": behavior}
    if behavior == "allow":
        if updated_input is not None:
            decision["updatedInput"] = updated_input
        if updated_permissions:
            decision["updatedPermissions"] = updated_permissions
    elif behavior == "deny":
        if message:
            decision["message"] = message
        if interrupt is not None:
            decision["interrupt"] = interrupt
    return {
        "hookSpecificOutput": {
            "hookEventName": "PermissionRequest",
            "decision": decision,
        }
    }


def _approval_signature(
    task_id: str | None,
    task_generation: str | int | None,
    tool_name: str,
    tool_input: dict[str, Any] | None = None,
    tool_input_signature: str | None = None,
) -> tuple[str, str, str, str]:
    return (
        str(task_id or ""),
        str(task_generation or ""),
        tool_name,
        str(tool_input_signature or approval_tool_input_signature(tool_input if tool_input is not None else {})),
    )


def _permission_rule(tool_name: str, tool_input: dict[str, Any]) -> dict[str, Any] | None:
    if not tool_name:
        return None
    if tool_name == "Bash":
        shell_command = tool_input.get("command") or tool_input.get("cmd") or tool_input.get("raw_command")
        if shell_command:
            return {"toolName": "Bash", "ruleContent": str(shell_command)}
    return {"toolName": tool_name}


def _session_allow_updates(tool_name: str, tool_input: dict[str, Any]) -> list[dict[str, Any]]:
    rule = _permission_rule(tool_name, tool_input)
    if not rule:
        return []
    return [
        {
            "type": "addRules",
            "rules": [rule],
            "behavior": "allow",
            "destination": "session",
        }
    ]


def _matching_approval(
    config: dict[str, Any],
    *,
    task_id: str | None,
    task_generation: str | int | None,
    tool_name: str,
    tool_input: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    state = load_approval_state(config)
    signature = _approval_signature(task_id, task_generation, tool_name, tool_input)
    pending_match = None
    history_match = None
    for item in state.get("pending", []):
        item_signature = _approval_signature(
            item.get("task_id"),
            item.get("task_generation"),
            item.get("tool_name") or "",
            tool_input_signature=item.get("tool_input_signature"),
        )
        if item_signature == signature:
            pending_match = item
    for item in reversed(state.get("history", [])):
        if item.get("resume_override_active"):
            # A non-remembered Claude approval is an exact one-shot grant.
            # It is decided only by find_resume_override + atomic consume,
            # never by generic resolved-history replay.
            continue
        item_signature = _approval_signature(
            item.get("task_id"),
            item.get("task_generation"),
            item.get("tool_name") or "",
            tool_input_signature=item.get("tool_input_signature"),
        )
        if item_signature == signature:
            history_match = item
            break
    return pending_match, history_match


def hook_mode(config: dict[str, Any], event_name: str, payload: dict[str, Any]) -> int:
    if event_name in {"PostToolUse", "PostToolUseFailure"}:
        tool_name = payload.get("tool_name") or payload.get("toolName") or ""
        tool_input = payload.get("tool_input") or payload.get("toolInput") or {}
        task_id = os.environ.get("ORCH_TASK_ID") or payload.get("task_id") or payload.get("taskId")
        task_generation = os.environ.get("ORCH_TASK_GENERATION") or payload.get("task_generation") or payload.get("taskGeneration")
        active_override = find_resume_override(
            config,
            task_id=task_id,
            task_generation=task_generation,
            tool_name=tool_name,
            tool_input=tool_input,
        )
        if active_override:
            consumed = consume_resume_override(
                config,
                approval_id=active_override["approval_id"],
                reason=f"{event_name}:{tool_name}",
            )
            log_event(
                config,
                event_name,
                {
                    **payload,
                    "resume_override": {
                        "approval_id": active_override.get("approval_id"),
                        "consumed_at": consumed.get("resume_override_consumed_at") if consumed else None,
                        "reason": consumed.get("resume_override_consumed_reason") if consumed else None,
                    },
                },
            )
            return 0
        log_event(config, event_name, payload)
        return 0

    if event_name in {"PreToolUse", "PermissionRequest"}:
        tool_name = payload.get("tool_name") or payload.get("toolName") or ""
        tool_input = payload.get("tool_input") or payload.get("toolInput") or {}
        task_id = os.environ.get("ORCH_TASK_ID") or payload.get("task_id") or payload.get("taskId")
        task_generation = os.environ.get("ORCH_TASK_GENERATION") or payload.get("task_generation") or payload.get("taskGeneration")
        active_override = find_resume_override(
            config,
            task_id=task_id,
            task_generation=task_generation,
            tool_name=tool_name,
            tool_input=tool_input,
        )
        pending_match, history_match = _matching_approval(
            config,
            task_id=task_id,
            task_generation=task_generation,
            tool_name=tool_name,
            tool_input=tool_input,
        )
        decision = evaluate_tool_request(
            tool_name,
            tool_input,
            config,
        )
        effective_decision = decision["decision"]
        effective_reason = decision["reason"]
        matched_approval_id = None

        if event_name == "PermissionRequest":
            if active_override:
                consumed = consume_resume_override(
                    config,
                    approval_id=active_override["approval_id"],
                    reason=f"PermissionRequest:{tool_name}",
                )
                if consumed is not None:
                    effective_decision = "allow"
                    effective_reason = active_override.get("note") or f"Resuming approved {tool_name} request."
                    matched_approval_id = active_override.get("approval_id")
                    log_event(
                        config,
                        event_name,
                        {
                            **payload,
                            "broker_decision": decision,
                            "effective_decision": effective_decision,
                            "effective_reason": effective_reason,
                            "matched_approval_id": matched_approval_id,
                            "updated_permissions": _session_allow_updates(tool_name, tool_input),
                            "resume_override_consumed_at": consumed.get("resume_override_consumed_at"),
                        },
                    )
                    emit_hook_response(
                        _permission_request_response(
                            "allow",
                            updated_input=tool_input,
                            updated_permissions=_session_allow_updates(tool_name, tool_input),
                        )
                    )
                    return 0
            if history_match:
                behavior = "allow" if history_match.get("decision") == "allow" else "deny"
                message = history_match.get("note") or decision["reason"]
                effective_decision = behavior
                effective_reason = message
                matched_approval_id = history_match.get("approval_id")
                log_event(
                    config,
                    event_name,
                    {
                        **payload,
                        "broker_decision": decision,
                        "effective_decision": effective_decision,
                        "effective_reason": effective_reason,
                        "matched_approval_id": matched_approval_id,
                    },
                )
                emit_hook_response(_permission_request_response(behavior, message=message))
                return 0
            if decision["decision"] in {"allow", "deny"}:
                behavior = "allow" if decision["decision"] == "allow" else "deny"
                effective_decision = behavior
                effective_reason = decision["reason"]
                log_event(
                    config,
                    event_name,
                    {
                        **payload,
                        "broker_decision": decision,
                        "effective_decision": effective_decision,
                        "effective_reason": effective_reason,
                    },
                )
                emit_hook_response(_permission_request_response(behavior, message=decision["reason"]))
            else:
                # Defer: log it but don't emit a hook response.
                # This lets Claude Code's native approval UI ask the user,
                # instead of silently denying.
                effective_decision = "defer"
                effective_reason = decision["reason"]
                log_event(
                    config,
                    event_name,
                    {
                        **payload,
                        "broker_decision": decision,
                        "effective_decision": effective_decision,
                        "effective_reason": effective_reason,
                    },
                )
                # No emit_hook_response → Claude Code falls through to its own prompt
            return 0

        if active_override:
            consumed = consume_resume_override(
                config,
                approval_id=active_override["approval_id"],
                reason=f"PreToolUse:{tool_name}",
            )
            if consumed is not None:
                effective_decision = "allow"
                effective_reason = active_override.get("note") or f"Resuming approved {tool_name} request."
                matched_approval_id = active_override.get("approval_id")
                log_event(
                    config,
                    event_name,
                    {
                        **payload,
                        "broker_decision": decision,
                        "effective_decision": effective_decision,
                        "effective_reason": effective_reason,
                        "matched_approval_id": matched_approval_id,
                        "resume_override_consumed_at": consumed.get("resume_override_consumed_at"),
                    },
                )
                emit_hook_response(_decision_response(event_name, "allow", effective_reason))
                return 0

        if decision["decision"] in {"allow", "deny"}:
            effective_decision = decision["decision"]
            effective_reason = decision["reason"]
            log_event(
                config,
                event_name,
                {
                    **payload,
                    "broker_decision": decision,
                    "effective_decision": effective_decision,
                    "effective_reason": effective_reason,
                },
            )
            emit_hook_response(_decision_response(event_name, decision["decision"], decision["reason"]))
            return 0

        if pending_match is None:
            approval_context = _approval_context(payload, config)
            try:
                validated_approval_binding(approval_context)
            except ValueError:
                approval_context = None
            if approval_context is not None:
                create_approval(
                    config,
                    {
                        **approval_context,
                        "tool_name": decision["tool_name"],
                        "tool_input": decision["tool_input"],
                        "risk_class": decision["risk_class"],
                        "suggested_rule": decision.get("suggested_rule"),
                        "request_payload": payload,
                        "broker_decision": decision,
                    },
                )
        effective_decision = "defer"
        effective_reason = decision["reason"]
        log_event(
            config,
            event_name,
            {
                **payload,
                "broker_decision": decision,
                "effective_decision": effective_decision,
                "effective_reason": effective_reason,
            },
        )
        emit_hook_response(_decision_response(event_name, "defer", decision["reason"]))
        return 0

    log_event(config, event_name, payload)
    return 0


def main() -> int:
    args = parse_args()
    config = load_broker_runtime_config()

    if args.command == "classify":
        print(classify_command(args.shell_command))
        return 0

    if args.command == "evaluate":
        tool_input = json.loads(args.tool_input_json)
        print(json.dumps(evaluate_tool_request(args.tool_name, tool_input, config), indent=2, ensure_ascii=False))
        return 0

    if args.command == "print-policy":
        print(json.dumps(_verified_claude_policy(config), indent=2, ensure_ascii=False))
        return 0

    if args.command == "remember":
        remember_rule(config, decision=args.decision, rule=args.rule)
        print(json.dumps({"ok": True, "decision": args.decision, "rule": args.rule}, ensure_ascii=False))
        return 0

    payload = hook_payload()
    if args.command == "log-hook":
        log_event(config, args.event_name, payload)
        return 0
    return hook_mode(config, args.event_name, payload)


if __name__ == "__main__":
    raise SystemExit(main())
