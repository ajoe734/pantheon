#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import shlex
import sys
from pathlib import Path
from typing import Any

THIS_DIR = Path(__file__).resolve().parent
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))

from approval_queue import consume_resume_override, create_approval, find_resume_override
from common import ROOT, load_config, load_json, utc_now, write_activity_log, write_json
from provider_permissions import CLAUDE_LOCAL_SETTINGS_PATH, _verified_claude_policy
from runtime_state import load_approval_state


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
    re.compile(r"^kill \d+"),
    re.compile(r"^pkill -f supervisor\.py"),
]
DEFER_BASH_PATTERNS = [
    re.compile(r"^git (add|commit|remote set-url|submodule)(\s|$)"),
    re.compile(r"^(curl|wget)(\s|$)"),
    re.compile(r"^(apt|apt-get)(\s|$)"),
    re.compile(r"^npm install(\s|$)"),
    re.compile(r"^pip install(\s|$)"),
    re.compile(r"^docker(\s|$)"),
]
DENY_BASH_PATTERNS = [
    re.compile(r"^git reset --hard"),
    re.compile(r"^git checkout --(\s|$)"),
    re.compile(r"^sudo(\s|$)"),
    re.compile(r"^rm -rf /\*?$"),
    re.compile(r"^chmod 777(\s|$)"),
]

SAFE_TOOLS = {"Read", "Grep", "Glob", "LS", "Task", "TodoRead", "TodoWrite", "ReadNotebook", "ToolSearch"}
EDIT_TOOLS = {"Edit", "MultiEdit", "Write"}
NETWORK_TOOLS = {"WebFetch", "WebSearch"}

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
    if _is_safe_pytest_install_command(shell_command):
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
    return "defer"


def _is_safe_python_one_liner(shell_command: str) -> bool:
    command = shell_command.strip()
    if not (command.startswith('python3 -c "') or command.startswith("python3 -c '")):
        return False
    if any(marker in command for marker in UNSAFE_PYTHON_ONE_LINER_MARKERS):
        return False
    return True


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


def _is_pytest_package_spec(token: str) -> bool:
    return bool(re.match(r"^pytest(?:[=<>!~].+)?$", token))


def _is_safe_pytest_install_command(shell_command: str) -> bool:
    command = shell_command.strip()
    if not command:
        return False

    segments = [segment.strip() for segment in command.split("&&")]
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
        if not token.startswith("-") and not re.match(r"^\d?>&\d+$", token)
    ]
    if not package_specs or not all(_is_pytest_package_spec(token) for token in package_specs):
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


def _paths_within_workspace(paths: list[Path]) -> bool:
    if not paths:
        return True
    allowed_roots = [ROOT, ROOT.parent / "pantheon"]
    for path in paths:
        resolved = path if path.is_absolute() else ROOT / path
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
    elif tool_name in EDIT_TOOLS:
        if _paths_within_workspace(_collect_paths(tool_input)):
            decision = "allow"
            reason = f"{tool_name} stays within the repository workspace."
            risk_class = "repo_write"
        else:
            decision = "deny"
            reason = f"{tool_name} targets a path outside {ROOT}."
            risk_class = "out_of_workspace"
    elif tool_name == "Bash":
        shell_command = tool_input.get("command") or tool_input.get("cmd") or tool_input.get("raw_command") or ""
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

    return {
        "decision": decision,
        "reason": reason,
        "risk_class": risk_class,
        "suggested_rule": suggested_rule,
        "tool_name": tool_name,
        "tool_input": tool_input,
        "evaluated_at": utc_now(),
        "policy_default_mode": config.get("providers", {}).get("claude", {}).get("approval", {}).get("rule_default_mode", "acceptEdits"),
    }


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
            "provider": "claude",
            "message": f"Remembered Claude rule in {decision}: {rule}",
            "decision": decision,
            "rule": rule,
        },
    )
    return settings


def _parse_permission_rule(rule: str) -> tuple[str | None, str | None]:
    match = re.match(r"^([A-Za-z0-9_]+)\((.*)\)$", rule)
    if match:
        return match.group(1), match.group(2)
    if rule:
        return rule, None
    return None, None


def _bash_rule_matches(rule_content: str, shell_command: str) -> bool:
    if "*" in rule_content:
        return fnmatch.fnmatchcase(shell_command, rule_content)
    return shell_command == rule_content


def _permission_rule_matches(rule: str, *, tool_name: str, tool_input: dict[str, Any]) -> bool:
    parsed_tool_name, rule_content = _parse_permission_rule(rule)
    if not parsed_tool_name or parsed_tool_name != tool_name:
        return False
    if rule_content is None:
        return True
    if tool_name == "Bash":
        shell_command = str(tool_input.get("command") or tool_input.get("cmd") or tool_input.get("raw_command") or "")
        return _bash_rule_matches(rule_content, shell_command)
    return False


def suspend_matching_rules(
    config: dict[str, Any],
    *,
    bucket: str,
    tool_name: str,
    tool_input: dict[str, Any],
) -> list[str]:
    settings = load_json(CLAUDE_LOCAL_SETTINGS_PATH, default={}) or {}
    permissions = settings.get("permissions", {})
    existing_rules = list(permissions.get(bucket, []) or [])
    removed_rules = [rule for rule in existing_rules if _permission_rule_matches(rule, tool_name=tool_name, tool_input=tool_input)]
    if not removed_rules:
        return []
    permissions[bucket] = [rule for rule in existing_rules if rule not in removed_rules]
    settings["permissions"] = permissions
    write_json(CLAUDE_LOCAL_SETTINGS_PATH, settings)
    write_activity_log(
        config,
        {
            "type": "permission_rule_temporary_removed",
            "provider": "claude",
            "message": f"Temporarily removed Claude {bucket} rule(s): {', '.join(removed_rules)}",
            "bucket": bucket,
            "rules": removed_rules,
        },
    )
    return removed_rules


def restore_rules(config: dict[str, Any], *, bucket: str, rules: list[str]) -> list[str]:
    if not rules:
        return []
    settings = load_json(CLAUDE_LOCAL_SETTINGS_PATH, default={}) or {}
    permissions = settings.get("permissions", {})
    existing_rules = list(permissions.get(bucket, []) or [])
    restored: list[str] = []
    for rule in rules:
        if rule not in existing_rules:
            existing_rules.append(rule)
            restored.append(rule)
    if not restored:
        return []
    permissions[bucket] = existing_rules
    settings["permissions"] = permissions
    write_json(CLAUDE_LOCAL_SETTINGS_PATH, settings)
    write_activity_log(
        config,
        {
            "type": "permission_rule_temporary_restored",
            "provider": "claude",
            "message": f"Restored Claude {bucket} rule(s): {', '.join(restored)}",
            "bucket": bucket,
            "rules": restored,
        },
    )
    return restored


def add_temporary_allow_rule(config: dict[str, Any], *, rule: str | None) -> bool:
    if not rule:
        return False
    settings = load_json(CLAUDE_LOCAL_SETTINGS_PATH, default={}) or {}
    permissions = settings.get("permissions", {})
    allow_rules = list(permissions.get("allow", []) or [])
    if rule in allow_rules:
        return False
    allow_rules.append(rule)
    permissions["allow"] = allow_rules
    settings["permissions"] = permissions
    write_json(CLAUDE_LOCAL_SETTINGS_PATH, settings)
    write_activity_log(
        config,
        {
            "type": "permission_rule_temporary_added",
            "provider": "claude",
            "message": f"Temporarily added Claude allow rule: {rule}",
            "rule": rule,
        },
    )
    return True


def remove_temporary_allow_rule(config: dict[str, Any], *, rule: str | None) -> bool:
    if not rule:
        return False
    settings = load_json(CLAUDE_LOCAL_SETTINGS_PATH, default={}) or {}
    permissions = settings.get("permissions", {})
    allow_rules = list(permissions.get("allow", []) or [])
    if rule not in allow_rules:
        return False
    permissions["allow"] = [entry for entry in allow_rules if entry != rule]
    settings["permissions"] = permissions
    write_json(CLAUDE_LOCAL_SETTINGS_PATH, settings)
    write_activity_log(
        config,
        {
            "type": "permission_rule_temporary_removed",
            "provider": "claude",
            "message": f"Removed temporary Claude allow rule: {rule}",
            "rule": rule,
        },
    )
    return True


def emit_hook_response(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False))


def log_event(config: dict[str, Any], event_name: str, payload: dict[str, Any]) -> None:
    message = payload.get("tool_name") or payload.get("toolName") or payload.get("raw") or event_name
    write_activity_log(
        config,
        {
            "type": "permission_hook",
            "provider": "claude",
            "message": f"{event_name}: {message}",
            "hook_event": event_name,
            "hook_payload": payload,
            "ts_local": utc_now(),
        },
    )


def _approval_timeout_seconds(config: dict[str, Any]) -> float:
    return float(
        config.get("providers", {})
        .get("claude", {})
        .get("broker", {})
        .get("approval_wait_seconds", 3600)
    )


def _approval_context(payload: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    return {
        "provider": "claude",
        "task_id": os.environ.get("ORCH_TASK_ID") or payload.get("task_id") or payload.get("taskId"),
        "worker_run_id": os.environ.get("ORCH_RUN_ID"),
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


def _approval_signature(session_id: str | None, tool_name: str, tool_input: dict[str, Any]) -> tuple[str | None, str, str]:
    return (
        session_id,
        tool_name,
        json.dumps(tool_input, sort_keys=True, ensure_ascii=False),
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
    session_id: str | None,
    tool_name: str,
    tool_input: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    state = load_approval_state(config)
    signature = _approval_signature(session_id, tool_name, tool_input)
    pending_match = None
    history_match = None
    for item in state.get("pending", []):
        item_signature = _approval_signature(item.get("session_id"), item.get("tool_name") or "", item.get("tool_input") or {})
        if item_signature == signature:
            pending_match = item
    for item in reversed(state.get("history", [])):
        item_signature = _approval_signature(item.get("session_id"), item.get("tool_name") or "", item.get("tool_input") or {})
        if item_signature == signature:
            history_match = item
            break
    return pending_match, history_match


def hook_mode(config: dict[str, Any], event_name: str, payload: dict[str, Any]) -> int:
    if event_name in {"PostToolUse", "PostToolUseFailure"}:
        tool_name = payload.get("tool_name") or payload.get("toolName") or ""
        tool_input = payload.get("tool_input") or payload.get("toolInput") or {}
        session_id = payload.get("session_id") or payload.get("sessionId")
        active_override = find_resume_override(
            config,
            session_id=session_id,
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
        session_id = payload.get("session_id") or payload.get("sessionId")
        active_override = find_resume_override(
            config,
            session_id=session_id,
            tool_name=tool_name,
            tool_input=tool_input,
        )
        pending_match, history_match = _matching_approval(
            config,
            session_id=session_id,
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
            create_approval(
                config,
                {
                    **_approval_context(payload, config),
                    "tool_name": decision["tool_name"],
                    "tool_input": decision["tool_input"],
                    "risk_class": decision["risk_class"],
                    "suggested_rule": decision.get("suggested_rule"),
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
    config = load_config()

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
