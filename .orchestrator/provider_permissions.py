#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 fallback.
    tomllib = None  # type: ignore[assignment]

from common import (
    ROOT,
    apply_claude_oauth_token_file,
    claude_auth_ready,
    claude_credentials_path,
    command_exists,
    config_path,
    load_config,
    load_json,
    run_command,
    to_bool,
    utc_now,
    write_json,
)

WORKSPACE_SETTINGS_PATH = ROOT / ".vscode" / "settings.json"
CLAUDE_LOCAL_SETTINGS_PATH = ROOT / ".claude" / "settings.local.json"
CLAUDE_LOCAL_EXAMPLE_PATH = ROOT / ".claude" / "settings.local.example.json"
GEMINI_SETTINGS_PATH = Path.home() / ".gemini" / "settings.json"
GEMINI_OAUTH_CREDS_PATH = Path.home() / ".gemini" / "oauth_creds.json"
CODEX_CONFIG_PATH = Path.home() / ".codex" / "config.toml"
CODEX_ALLOWED_SERVICE_TIERS = ("fast", "flex")
EXTENSIONS_DIR = Path.home() / ".vscode-server" / "extensions"
COPILOT_CONFIG_DIR = Path.home() / ".copilot"
COPILOT_CONFIG_PATH = COPILOT_CONFIG_DIR / "config.json"


def _find_extension(prefix: str) -> tuple[Path | None, str | None]:
    matches = sorted(EXTENSIONS_DIR.glob(f"{prefix}-*"))
    if not matches:
        return None, None
    path = matches[-1]
    version = path.name[len(prefix) + 1 :]
    return path, version


def _load_package_json(path: Path | None) -> dict[str, Any]:
    if not path:
        return {}
    return load_json(path / "package.json", default={}) or {}


def _workspace_settings() -> dict[str, Any]:
    return load_json(WORKSPACE_SETTINGS_PATH, default={}) or {}


def _claude_local_settings() -> dict[str, Any]:
    return load_json(CLAUDE_LOCAL_SETTINGS_PATH, default={}) or {}


def _gemini_home(config: dict[str, Any] | None = None, provider_id: str = "gemini") -> Path:
    provider = ((config or {}).get("providers", {}).get(provider_id, {}) or {}).get("gemini", {}) or {}
    home = str(provider.get("config_home") or provider.get("home") or "").strip()
    return Path(os.path.expanduser(home)) if home else Path.home()


def _gemini_settings_path(config: dict[str, Any] | None = None, provider_id: str = "gemini") -> Path:
    return _gemini_home(config, provider_id) / ".gemini" / "settings.json"


def _gemini_oauth_creds_path(config: dict[str, Any] | None = None, provider_id: str = "gemini") -> Path:
    return _gemini_home(config, provider_id) / ".gemini" / "oauth_creds.json"


def _gemini_runtime_env(config: dict[str, Any] | None = None, provider_id: str = "gemini") -> dict[str, str]:
    env = dict(os.environ)
    provider = (config or {}).get("providers", {}).get(provider_id, {}) or {}
    for block_name in ("runtime", "gemini"):
        block = provider.get(block_name, {}) or {}
        for key, value in (block.get("env", {}) or {}).items():
            if value is None:
                continue
            env[str(key)] = os.path.expanduser(str(value))
    return env


def _codex_home(config: dict[str, Any] | None = None, provider_id: str = "codex") -> Path:
    provider = ((config or {}).get("providers", {}).get(provider_id, {}) or {}).get("codex", {}) or {}
    home = str(provider.get("codex_home") or provider.get("config_home") or "").strip()
    return Path(os.path.expanduser(home)) if home else Path.home() / ".codex"


def _codex_config_path(config: dict[str, Any] | None = None, provider_id: str = "codex") -> Path:
    return _codex_home(config, provider_id) / "config.toml"


def codex_config_health(config: dict[str, Any] | None = None, provider_id: str = "codex") -> dict[str, Any]:
    path = _codex_config_path(config, provider_id)
    result: dict[str, Any] = {
        "valid": True,
        "path": str(path),
        "checks": {"service_tier": None},
        "allowed_service_tiers": list(CODEX_ALLOWED_SERVICE_TIERS),
    }
    if not path.exists():
        result["notes"] = "Codex config file is absent; CLI defaults apply."
        return result
    if tomllib is None:
        result["notes"] = "Python tomllib is unavailable; Codex config schema preflight skipped."
        return result

    try:
        payload = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        result.update(
            {
                "valid": False,
                "error": f"Codex config {path} cannot be parsed: {exc}",
            }
        )
        return result

    service_tier = payload.get("service_tier")
    result["checks"]["service_tier"] = service_tier
    if service_tier in (None, ""):
        return result
    if not isinstance(service_tier, str):
        result.update(
            {
                "valid": False,
                "error": (
                    f"Codex config {path} has non-string service_tier={service_tier!r}; "
                    f"installed Codex CLI accepts {', '.join(CODEX_ALLOWED_SERVICE_TIERS)}."
                ),
            }
        )
        return result
    normalized = service_tier.strip().lower()
    if normalized not in CODEX_ALLOWED_SERVICE_TIERS:
        result.update(
            {
                "valid": False,
                "error": (
                    f"Codex config {path} has unsupported service_tier={service_tier!r}; "
                    f"installed Codex CLI accepts {', '.join(CODEX_ALLOWED_SERVICE_TIERS)}."
                ),
            }
        )
    return result


def _gemini_settings(config: dict[str, Any] | None = None, provider_id: str = "gemini") -> dict[str, Any]:
    return load_json(_gemini_settings_path(config, provider_id), default={}) or {}


def _truthy_env(name: str, env: dict[str, str] | None = None) -> bool:
    source = env if env is not None else os.environ
    return source.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _gemini_env_auth_type(env: dict[str, str] | None = None) -> str | None:
    if _truthy_env("GOOGLE_GENAI_USE_GCA", env):
        return "oauth-personal"
    if _truthy_env("GEMINI_CLI_USE_COMPUTE_ADC", env):
        return "compute-default-credentials"
    if _truthy_env("GOOGLE_GENAI_USE_VERTEXAI", env):
        return "vertex-ai"
    source = env if env is not None else os.environ
    if source.get("GEMINI_API_KEY"):
        return "gemini-api-key"
    return None


def _gemini_selected_auth_type(
    settings: dict[str, Any],
    *,
    oauth_creds_path: Path = GEMINI_OAUTH_CREDS_PATH,
    env: dict[str, str] | None = None,
) -> str | None:
    return (
        _gemini_env_auth_type(env)
        or settings.get("security", {}).get("auth", {}).get("selectedType")
        or ("oauth-personal" if oauth_creds_path.exists() else None)
    )


def _gemini_auth_ready(
    settings: dict[str, Any],
    *,
    oauth_creds_path: Path = GEMINI_OAUTH_CREDS_PATH,
    env: dict[str, str] | None = None,
) -> bool:
    source = env if env is not None else os.environ
    auth_type = _gemini_selected_auth_type(settings, oauth_creds_path=oauth_creds_path, env=source)
    if auth_type == "oauth-personal":
        return oauth_creds_path.exists()
    if auth_type == "gemini-api-key":
        return bool(source.get("GEMINI_API_KEY"))
    if auth_type == "vertex-ai":
        return bool(
            source.get("GOOGLE_API_KEY")
            or (source.get("GOOGLE_CLOUD_PROJECT") and source.get("GOOGLE_CLOUD_LOCATION"))
        )
    if auth_type == "compute-default-credentials":
        if source.get("GOOGLE_APPLICATION_CREDENTIALS"):
            return True
        gcloud = command_exists("gcloud")
        return bool(gcloud) and run_command([gcloud, "auth", "application-default", "print-access-token"]).returncode == 0
    return False


def _read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="ignore")


def _code_cli_info() -> dict[str, Any]:
    binary = command_exists("code")
    if not binary:
        return {"available": False, "version": None, "code_chat_available": False, "notes": "`code` CLI not found"}
    version_output = run_command(["code", "--version"])
    version = (version_output.stdout or "").splitlines()[0].strip() if version_output.stdout else None
    chat_help = run_command(["code", "chat", "--help"])
    chat_output = (chat_help.stdout or "") + (chat_help.stderr or "")
    code_chat_available = "Usage: code chat" in chat_output
    return {
        "available": True,
        "version": version,
        "code_chat_available": code_chat_available,
        "notes": "Verified via local CLI help output.",
    }


def _command_help_contains(command: list[str], needle: str) -> bool:
    result = run_command(command)
    output = (result.stdout or "") + (result.stderr or "")
    return needle in output


def _gh_version(binary: str | None) -> tuple[int, int, int] | None:
    if not binary:
        return None
    output = (run_command([binary, "--version"]).stdout or "").splitlines()
    if not output:
        return None
    match = __import__("re").search(r"(\d+)\.(\d+)\.(\d+)", output[0])
    if not match:
        return None
    return tuple(int(part) for part in match.groups())


def _json_command(command: list[str]) -> dict[str, Any]:
    result = run_command(command)
    if result.returncode != 0 or not result.stdout:
        return {}
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}


def _provider_runtime_env(config: dict[str, Any], provider_id: str) -> dict[str, str]:
    env = dict(os.environ)
    runtime = (config.get("providers", {}).get(provider_id, {}) or {}).get("runtime", {}) or {}
    home = str(runtime.get("home") or "").strip()
    if home:
        env["HOME"] = os.path.expanduser(home)
    extra_env = runtime.get("env", {}) or {}
    for key, value in extra_env.items():
        if value is None:
            continue
        env[str(key)] = os.path.expanduser(str(value))
    apply_claude_oauth_token_file(env, runtime)
    return env


def _gh_auth_token(binary: str | None) -> str | None:
    if not binary:
        return None
    result = run_command([binary, "auth", "token"])
    token = (result.stdout or "").strip()
    return token or None


def _gh_auth_ready(binary: str | None) -> bool:
    return bool(_gh_auth_token(binary))


def _copilot_config_auth_ready() -> bool:
    if not COPILOT_CONFIG_PATH.exists():
        return False
    for candidate in ("oauth.json", "auth.json", "credentials.json", "hosts.json"):
        if (COPILOT_CONFIG_DIR / candidate).exists():
            return True
    payload = load_json(COPILOT_CONFIG_PATH, default={}) or {}
    return any(key != "firstLaunchAt" and value not in (None, "", {}, []) for key, value in payload.items())


def _copilot_auth_ready(gh_binary: str | None) -> bool:
    if _gh_auth_token(gh_binary):
        return True
    if any(os.environ.get(name) for name in ("COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN")):
        return True
    return _copilot_config_auth_ready()


def _configured_provider_binary(config: dict[str, Any], provider: str, section: str, default: str) -> str | None:
    provider_settings = (config.get("providers", {}).get(provider, {}) or {}).get(section, {})
    return command_exists(provider_settings.get("cli") or default)


def _custom_agents_info() -> dict[str, Any]:
    copilot_path, version = _find_extension("github.copilot-chat")
    reference_path = None
    supported = False
    if copilot_path:
        candidate = copilot_path / "assets" / "prompts" / "skills" / "agent-customization" / "references" / "agents.md"
        if candidate.exists():
            reference_path = candidate
            supported = True
    return {
        "supported": supported,
        "verified": "verified" if supported else "unavailable",
        "workspace_path": str(ROOT / ".github" / "agents"),
        "reference_path": str(reference_path) if reference_path else None,
        "extension_version": version,
    }


def _relevant_extensions() -> list[dict[str, Any]]:
    prefixes = [
        "anthropic.claude-code",
        "google.geminicodeassist",
        "openai.chatgpt",
        "github.copilot-chat",
    ]
    results: list[dict[str, Any]] = []
    for prefix in prefixes:
        path, version = _find_extension(prefix)
        if path:
            results.append({"id": prefix, "version": version, "path": str(path)})
    return results


def _workspace_setting(settings: dict[str, Any], key: str) -> Any:
    return settings.get(key)


def _verified_claude_policy(config: dict[str, Any]) -> dict[str, Any]:
    approval = config.get("providers", {}).get("claude", {}).get("approval", {})
    safe_allow = [
        "Bash(pwd)",
        "Bash(ls *)",
        "Bash(find *)",
        "Bash(rg *)",
        "Bash(cat *)",
        "Bash(sed *)",
        "Bash(head *)",
        "Bash(tail *)",
        "Bash(git status*)",
        "Bash(git diff*)",
        "Bash(git show*)",
        "Bash(git submodule status*)",
        "Bash(git push *)",
        "Bash(gh issue comment *)",
        "Bash(gh pr create *)",
        "Bash(bash scripts/ai-status.sh *)",
        "Bash(AI_NAME=* bash scripts/ai-status.sh *)",
        "Bash(AI_NAME=* bash */scripts/ai-status.sh *)",
        "Bash(bash */scripts/ai-status.sh *)",
        "Bash(python3 scripts/ai_status.py *)",
        "Bash(python3 */scripts/ai_status.py *)",
        "Bash(cd * && python3 scripts/ai_status.py *)",
        "Bash(cd * && python3 */scripts/ai_status.py *)",
        "Bash(cd * && bash scripts/ai-status.sh *)",
        "Bash(cd * && bash */scripts/ai-status.sh *)",
        "Bash(python3 -m unittest discover *)",
        "Bash(cd * && python3 -m unittest discover *)",
        "Bash(python3 -m pytest*)",
        "Bash(cd * && python3 -m pytest*)",
        "Bash(pytest*)",
        "Bash(cd * && pytest*)",
        "Bash(apt-get install*python3-pytest*)",
        "Bash(apt install*python3-pytest*)",
        "Bash(python3 -m pip install*pytest*)",
        "Bash(pip install*pytest*)",
        "Bash(pip3 install*pytest*)",
        "Bash(npm test*)",
        "Bash(cd * && npm test*)",
        "Bash(npm run test*)",
        "Bash(cd * && npm run test*)",
        "Bash(cargo test*)",
        "Bash(cd * && cargo test*)",
        "Bash(go test*)",
        "Bash(cd * && go test*)",
        "Bash(python3 -m py_compile *)",
        "Bash(cd * && python3 -m py_compile *)",
        "Bash(python3 */smoke_test.py*)",
        "Bash(cd * && python3 smoke_test.py*)",
        "Bash(docker ps*)",
        "Bash(docker images*)",
        "Bash(docker inspect*)",
        "Bash(docker logs*)",
        "Bash(docker compose ps*)",
        "Bash(docker compose images*)",
        "Bash(docker compose * config*)",
        "Bash(docker exec * python3 -c \"import*\")",
        "Bash(docker exec * python -c \"import*\")",
        "Bash(AI_NAME=* python3 scripts/ai_status.py *)",
        "Bash(AI_NAME=* python3 */scripts/ai_status.py *)",
        "Bash(AI_NAME=* cd * && python3 scripts/ai_status.py *)",
        "Bash(AI_NAME=* cd * && python3 */scripts/ai_status.py *)",
    ]
    ask = [
        "Bash(curl *)",
        "Bash(wget *)",
        "Bash(apt *)",
        "Bash(npm install *)",
        "Bash(pip install *)",
        "Bash(docker build *)",
        "Bash(docker pull *)",
        "Bash(docker push *)",
        "Bash(docker run *)",
        "Bash(docker rm *)",
        "Bash(docker stop *)",
        "Bash(docker start *)",
        "Bash(docker restart *)",
        "Bash(docker kill *)",
        "Bash(docker cp *)",
        "Bash(docker compose up *)",
        "Bash(docker compose down *)",
        "Bash(docker compose run *)",
        "Bash(docker compose exec *)",
    ]
    deny = [
        "Bash(git reset --hard*)",
        "Bash(git checkout -- *)",
        "Bash(sudo *)",
        "Bash(rm -rf /*)",
        "Bash(chmod 777 *)",
    ]
    return {
        "defaultMode": approval.get("rule_default_mode", "acceptEdits"),
        "disableBypassPermissionsMode": "disable" if approval.get("disable_bypass_permissions", True) else None,
        "allow": safe_allow,
        "ask": ask,
        "deny": deny,
    }


def _verified_claude_hooks() -> dict[str, Any]:
    broker_path = ROOT / ".orchestrator" / "permission_broker.py"
    command = f"python3 {broker_path} hook"
    hook = lambda event: [{"hooks": [{"type": "command", "command": f"{command} {event}", "shell": "bash"}]}]
    return {
        "PreToolUse": hook("PreToolUse"),
        "PermissionRequest": hook("PermissionRequest"),
        "PermissionDenied": hook("PermissionDenied"),
        "PostToolUse": hook("PostToolUse"),
        "SessionStart": hook("SessionStart"),
        "SessionEnd": hook("SessionEnd"),
        "Stop": hook("Stop"),
    }


def desired_workspace_settings(config: dict[str, Any]) -> dict[str, Any]:
    claude_approval = config.get("providers", {}).get("claude", {}).get("approval", {})
    gemini_approval = config.get("providers", {}).get("gemini", {}).get("approval", {})
    return {
        "claudeCode.initialPermissionMode": claude_approval.get("workspace_permission_mode", "acceptEdits"),
        "claudeCode.allowDangerouslySkipPermissions": to_bool(claude_approval.get("allow_dangerous_skip", False)),
        "github.copilot.chat.backgroundAgent.enabled": True,
        "github.copilot.chat.cloudAgent.enabled": True,
        "github.copilot.chat.claudeAgent.enabled": True,
        "github.copilot.chat.claudeAgent.allowDangerouslySkipPermissions": to_bool(
            claude_approval.get("copilot_allow_dangerous_skip", False)
        ),
        "github.copilot.chat.reviewAgent.enabled": True,
        "geminicodeassist.enable": True,
        "geminicodeassist.agentYoloMode": to_bool(gemini_approval.get("workspace_agent_yolo_mode", False)),
    }


def desired_claude_local_settings(config: dict[str, Any], current: dict[str, Any] | None = None) -> dict[str, Any]:
    existing = current or {}
    permissions = existing.get("permissions", {})
    verified_policy = _verified_claude_policy(config)
    allow_values = list(dict.fromkeys([*(permissions.get("allow", []) or []), *verified_policy["allow"]]))
    ask_values = list(dict.fromkeys([*(permissions.get("ask", []) or []), *verified_policy["ask"]]))
    deny_values = list(dict.fromkeys([*(permissions.get("deny", []) or []), *verified_policy["deny"]]))
    allow_set = set(allow_values)
    ask_values = [value for value in ask_values if value not in allow_set]
    ask_set = set(ask_values)
    deny_values = [value for value in deny_values if value not in allow_set and value not in ask_set]
    next_permissions = {
        **permissions,
        "allow": allow_values,
        "ask": ask_values,
        "deny": deny_values,
        "defaultMode": verified_policy["defaultMode"],
    }
    if verified_policy["disableBypassPermissionsMode"]:
        next_permissions["disableBypassPermissionsMode"] = verified_policy["disableBypassPermissionsMode"]
    hooks = existing.get("hooks", {})
    merged_hooks = {**hooks}
    legacy_hook_snippets = (
        "permission_broker.py hook",
        "permission_broker.py log-hook",
    )
    for event, hook_entries in _verified_claude_hooks().items():
        existing_entries = [
            entry
            for entry in hooks.get(event, [])
            if not any(snippet in json.dumps(entry, sort_keys=True) for snippet in legacy_hook_snippets)
        ]
        serialized_existing = {json.dumps(entry, sort_keys=True) for entry in existing_entries}
        merged = list(existing_entries)
        for entry in hook_entries:
            payload = json.dumps(entry, sort_keys=True)
            if payload not in serialized_existing:
                merged.append(entry)
        merged_hooks[event] = merged
    return {**existing, "permissions": next_permissions, "hooks": merged_hooks}


def desired_gemini_settings(config: dict[str, Any], provider_id: str = "gemini") -> dict[str, Any]:
    approval = config.get("providers", {}).get(provider_id, {}).get("approval", {})
    gemini_runtime = config.get("providers", {}).get(provider_id, {}).get("gemini", {}) or {}
    model = str(gemini_runtime.get("model") or "").strip()
    approval_mode = str(approval.get("default_approval_mode", "auto_edit") or "auto_edit")
    settings_approval_mode = "auto_edit" if approval_mode == "yolo" else approval_mode
    auth_type = _gemini_selected_auth_type(
        _gemini_settings(config, provider_id),
        oauth_creds_path=_gemini_oauth_creds_path(config, provider_id),
        env=_gemini_runtime_env(config, provider_id),
    )
    security: dict[str, Any] = {
        "enablePermanentToolApproval": to_bool(approval.get("enable_permanent_tool_approval", True)),
        "autoAddToPolicyByDefault": to_bool(approval.get("auto_add_to_policy_by_default", True)),
        "disableYoloMode": to_bool(approval.get("disable_yolo_mode", False)),
        "disableAlwaysAllow": to_bool(approval.get("disable_always_allow", False)),
    }
    if auth_type:
        security["auth"] = {"selectedType": auth_type}
    desired = {
        "general": {
            "defaultApprovalMode": settings_approval_mode,
        },
        "security": security,
    }
    if model:
        desired["model"] = {"name": model}
    return desired


def _claude_provider_report(
    config: dict[str, Any],
    *,
    provider_id: str,
    claude_path: Path | None,
    claude_version: str | None,
    claude_local: dict[str, Any],
    claude_permissions: dict[str, Any],
    workspace_settings: dict[str, Any],
    claude_applied: bool,
) -> dict[str, Any]:
    provider_settings = config.get("providers", {}).get(provider_id, {}) or {}
    runtime_env = _provider_runtime_env(config, provider_id)
    provider_binary = _configured_provider_binary(config, provider_id, "runtime", "claude")
    provider_auth_ready = claude_auth_ready(provider_binary, env=runtime_env, refresh_if_needed=False)
    provider_home = str((provider_settings.get("runtime", {}) or {}).get("home") or "").strip()
    credentials_path = claude_credentials_path(runtime_env)
    installed = bool(provider_binary or claude_path or claude_local or credentials_path.exists())
    notes = [
        "Verified settings keys from the installed Claude Code extension package and schema.",
        "Claude CLI worker support becomes active when the `claude` binary is on PATH and authenticated; otherwise the adapter falls back to inbox delivery.",
        "The local approval broker uses committed Claude hooks plus the orchestrator approval queue instead of VS Code UI injection.",
    ]
    if provider_id != "claude":
        notes.append(f"Provider `{provider_id}` uses its own Claude runtime HOME/profile when configured.")
    return {
        "installed": installed,
        "host_layer": "CLI + VS Code extension" if provider_binary and claude_path else ("CLI" if provider_binary else "VS Code extension"),
        "delivery_mode": provider_settings.get("delivery_mode", "claude_cli"),
        "approval_mode": claude_permissions.get("defaultMode")
        or _workspace_setting(workspace_settings, "claudeCode.initialPermissionMode")
        or "default",
        "persistent_allow_supported": True,
        "default_auto_approve_supported": True,
        "full_access_supported": True,
        "per_tool_allow_supported": True,
        "local_cli_worker_supported": bool(provider_binary and provider_auth_ready),
        "vscode_link_supported": bool(claude_path),
        "cloud_agent_supported": False,
        "supports_auto_approve": bool(provider_binary and provider_auth_ready),
        "supports_defer_resume": bool(provider_binary),
        "auth_ready": provider_auth_ready,
        "supported_models": claude_local.get("availableModels", []) or [],
        "selected_model": claude_local.get("model"),
        "applied": claude_applied,
        "verified": "verified" if installed else "unavailable",
        "version": claude_version,
        "paths": {
            "binary": provider_binary,
            "extension": str(claude_path) if claude_path else None,
            "workspace_settings": str(WORKSPACE_SETTINGS_PATH),
            "project_settings": str(CLAUDE_LOCAL_SETTINGS_PATH),
            "mcp_config": str(config_path(config, "claude_mcp_config")),
            "home": os.path.expanduser(provider_home) if provider_home else None,
            "credentials": str(credentials_path),
        },
        "settings": {
            "claudeCode.initialPermissionMode": _workspace_setting(workspace_settings, "claudeCode.initialPermissionMode"),
            "claudeCode.allowDangerouslySkipPermissions": _workspace_setting(
                workspace_settings, "claudeCode.allowDangerouslySkipPermissions"
            ),
            "permissions.defaultMode": claude_permissions.get("defaultMode"),
            "permissions.allow_count": len(claude_permissions.get("allow", []) or []),
            "permissions.ask_count": len(claude_permissions.get("ask", []) or []),
            "permissions.deny_count": len(claude_permissions.get("deny", []) or []),
            "hooks.PreToolUse": bool(claude_local.get("hooks", {}).get("PreToolUse")),
            "hooks.PermissionRequest": bool(claude_local.get("hooks", {}).get("PermissionRequest")),
        },
        "notes": notes,
    }


def _gemini_provider_report(
    config: dict[str, Any],
    *,
    provider_id: str,
    gemini_path: Path | None,
    gemini_version: str | None,
    workspace_settings: dict[str, Any],
    gemini_applied: bool,
) -> dict[str, Any]:
    provider_config = (config.get("providers", {}).get(provider_id, {}) or {})
    gemini_runtime = provider_config.get("gemini", {}) or {}
    runtime_approval_mode = (provider_config.get("approval", {}) or {}).get("default_approval_mode")
    selected_model = str(gemini_runtime.get("model") or "").strip() or None
    provider_binary = _configured_provider_binary(config, provider_id, "gemini", "gemini")
    provider_settings = _gemini_settings(config, provider_id)
    oauth_creds_path = _gemini_oauth_creds_path(config, provider_id)
    runtime_env = _gemini_runtime_env(config, provider_id)
    auth_ready = _gemini_auth_ready(provider_settings, oauth_creds_path=oauth_creds_path, env=runtime_env)
    auth_type = _gemini_selected_auth_type(provider_settings, oauth_creds_path=oauth_creds_path, env=runtime_env)
    installed = bool(gemini_path or provider_binary)
    notes = [
        "Verified CLI approval flags and settings schema from the locally installed Gemini CLI package.",
        "YOLO can be enabled either per-run with CLI flags or through the VS Code extension setting.",
        "Gemini CLI non-interactive auth requires either a selected auth type in ~/.gemini/settings.json or one of the documented environment-variable auth paths.",
    ]
    if provider_id != "gemini":
        notes.append(f"Provider `{provider_id}` uses its configured Gemini CLI home/env profile when provided.")
    return {
        "installed": installed,
        "host_layer": "VS Code extension + CLI" if gemini_path and provider_binary else ("CLI" if provider_binary else "VS Code extension"),
        "delivery_mode": (config.get("providers", {}).get(provider_id, {}) or {}).get("delivery_mode", "gemini"),
        "approval_mode": runtime_approval_mode
        or provider_settings.get("general", {}).get("defaultApprovalMode")
        or "default",
        "persistent_allow_supported": True,
        "default_auto_approve_supported": True,
        "full_access_supported": True,
        "per_tool_allow_supported": True,
        "local_cli_worker_supported": bool(provider_binary and auth_ready),
        "vscode_link_supported": bool(gemini_path),
        "cloud_agent_supported": False,
        "supports_auto_approve": bool(provider_binary and auth_ready),
        "supports_defer_resume": False,
        "supported_models": [selected_model] if selected_model else [],
        "selected_model": selected_model,
        "auth_ready": auth_ready,
        "applied": gemini_applied,
        "verified": "verified" if installed else "unavailable",
        "version": gemini_version,
        "paths": {
            "extension": str(gemini_path) if gemini_path else None,
            "binary": provider_binary,
            "workspace_settings": str(WORKSPACE_SETTINGS_PATH),
            "home": str(_gemini_home(config, provider_id)),
            "cli_settings": str(_gemini_settings_path(config, provider_id)),
            "oauth_creds": str(oauth_creds_path) if oauth_creds_path.exists() else None,
        },
        "settings": {
            "geminicodeassist.agentYoloMode": _workspace_setting(workspace_settings, "geminicodeassist.agentYoloMode"),
            "general.defaultApprovalMode": provider_settings.get("general", {}).get("defaultApprovalMode"),
            "runtime.defaultApprovalMode": runtime_approval_mode,
            "security.enablePermanentToolApproval": provider_settings.get("security", {}).get("enablePermanentToolApproval"),
            "security.autoAddToPolicyByDefault": provider_settings.get("security", {}).get("autoAddToPolicyByDefault"),
            "security.disableYoloMode": provider_settings.get("security", {}).get("disableYoloMode"),
            "security.auth.selectedType": auth_type,
            "gemini.model": selected_model,
            "env.GOOGLE_CLOUD_PROJECT": runtime_env.get("GOOGLE_CLOUD_PROJECT"),
            "env.GOOGLE_CLOUD_PROJECT_ID": runtime_env.get("GOOGLE_CLOUD_PROJECT_ID"),
            "env.GOOGLE_CLOUD_LOCATION": runtime_env.get("GOOGLE_CLOUD_LOCATION"),
        },
        "notes": notes,
    }


def provider_capabilities(config: dict[str, Any] | None = None) -> dict[str, Any]:
    config = config or load_config()
    from adapters import build_adapter

    code_cli = _code_cli_info()
    workspace_settings = _workspace_settings()
    claude_path, claude_version = _find_extension("anthropic.claude-code")
    gemini_path, gemini_version = _find_extension("google.geminicodeassist")
    openai_path, openai_version = _find_extension("openai.chatgpt")
    copilot_path, copilot_version = _find_extension("github.copilot-chat")
    claude_local = _claude_local_settings()
    gemini_settings = _gemini_settings(config, "gemini")
    gemini_env = _gemini_runtime_env(config, "gemini")
    gemini_auth_ready = _gemini_auth_ready(gemini_settings, oauth_creds_path=_gemini_oauth_creds_path(config, "gemini"), env=gemini_env)
    gemini_auth_type = _gemini_selected_auth_type(gemini_settings, oauth_creds_path=_gemini_oauth_creds_path(config, "gemini"), env=gemini_env)
    custom_agents = _custom_agents_info()

    claude_permissions = claude_local.get("permissions", {})
    desired_workspace = desired_workspace_settings(config)
    desired_claude = desired_claude_local_settings(config, current=claude_local)
    desired_gemini = desired_gemini_settings(config, "gemini")
    codex_binary = command_exists("codex")
    gemini_binary = _configured_provider_binary(config, "gemini", "gemini", "gemini")
    copilot_binary = _configured_provider_binary(config, "copilot", "local", "copilot")
    gh_binary = command_exists(config.get("providers", {}).get("copilot", {}).get("cloud", {}).get("cli") or "gh")
    gh_version = _gh_version(gh_binary)
    gh_auth_ready = _gh_auth_ready(gh_binary)
    copilot_auth_ready = _copilot_auth_ready(gh_binary)
    copilot_settings = config.get("providers", {}).get("copilot", {})
    copilot_model_preference = copilot_settings.get("model_preference", {})
    gemini_installed = bool(gemini_path or gemini_binary)
    codex_installed = bool(openai_path or codex_binary)
    copilot_installed = bool(copilot_path or copilot_binary or gh_binary)

    claude_applied = (
        _workspace_setting(workspace_settings, "claudeCode.initialPermissionMode") == desired_workspace["claudeCode.initialPermissionMode"]
        and _workspace_setting(workspace_settings, "claudeCode.allowDangerouslySkipPermissions")
        == desired_workspace["claudeCode.allowDangerouslySkipPermissions"]
        and claude_permissions.get("defaultMode") == desired_claude["permissions"]["defaultMode"]
        and bool(claude_local.get("hooks", {}).get("PreToolUse"))
    )
    gemini_applied = (
        _workspace_setting(workspace_settings, "geminicodeassist.agentYoloMode") == desired_workspace["geminicodeassist.agentYoloMode"]
        and gemini_settings.get("general", {}).get("defaultApprovalMode")
        == desired_gemini["general"]["defaultApprovalMode"]
        and gemini_settings.get("security", {}).get("enablePermanentToolApproval")
        == desired_gemini["security"]["enablePermanentToolApproval"]
        and gemini_settings.get("security", {}).get("autoAddToPolicyByDefault")
        == desired_gemini["security"]["autoAddToPolicyByDefault"]
        and (
            not desired_gemini["security"].get("auth", {}).get("selectedType")
            or gemini_settings.get("security", {}).get("auth", {}).get("selectedType")
            == desired_gemini["security"]["auth"]["selectedType"]
        )
    )

    codex_profile = config.get("providers", {}).get("codex", {}).get("codex", {})
    codex_applied = (
        codex_profile.get("ask_for_approval", "never") == "never"
        and codex_profile.get("sandbox_mode", "workspace-write") == "workspace-write"
    )
    copilot_applied = (
        _workspace_setting(workspace_settings, "github.copilot.chat.backgroundAgent.enabled")
        == desired_workspace["github.copilot.chat.backgroundAgent.enabled"]
        and _workspace_setting(workspace_settings, "github.copilot.chat.cloudAgent.enabled")
        == desired_workspace["github.copilot.chat.cloudAgent.enabled"]
        and _workspace_setting(workspace_settings, "github.copilot.chat.claudeAgent.enabled")
        == desired_workspace["github.copilot.chat.claudeAgent.enabled"]
    )
    claude_provider_ids = list(
        dict.fromkeys(
            [
                "claude",
                *[
                    provider_id
                    for provider_id, settings in (config.get("providers", {}) or {}).items()
                    if provider_id != "claude" and (settings or {}).get("delivery_mode") == "claude_cli"
                ],
            ]
        )
    )
    gemini_provider_ids = list(
        dict.fromkeys(
            [
                "gemini",
                *[
                    provider_id
                    for provider_id, settings in (config.get("providers", {}) or {}).items()
                    if provider_id != "gemini" and (settings or {}).get("delivery_mode") == "gemini"
                ],
            ]
        )
    )
    codex_provider_ids = list(
        dict.fromkeys(
            [
                "codex",
                *[
                    provider_id
                    for provider_id, settings in (config.get("providers", {}) or {}).items()
                    if provider_id != "codex" and (settings or {}).get("delivery_mode") == "codex"
                ],
            ]
        )
    )

    def codex_provider_report(provider_id: str) -> dict[str, Any]:
        provider_settings = config.get("providers", {}).get(provider_id, {}) or {}
        profile = provider_settings.get("codex", {}) or codex_profile
        config_path_for_provider = _codex_config_path(config, provider_id)
        config_health = codex_config_health(config, provider_id)
        applied = (
            profile.get("ask_for_approval", "never") == "never"
            and profile.get("sandbox_mode", "workspace-write") == "workspace-write"
        )
        notes = [
            "Verified CLI flags from the locally installed Codex CLI help output.",
            "No verified persistent approval config keys were found in local extension metadata, so auto-approve is applied per orchestrated run rather than globally.",
        ]
        if not config_health.get("valid", True):
            notes.insert(0, str(config_health.get("error") or "Codex config is invalid."))
        return {
            "installed": codex_installed,
            "host_layer": "CLI + VS Code extension" if openai_path and codex_binary else ("CLI" if codex_binary else "VS Code extension"),
            "delivery_mode": "codex",
            "quota_group": provider_settings.get("quota_group"),
            "approval_mode": f"orchestrator:{profile.get('ask_for_approval', 'never')}",
            "persistent_allow_supported": False,
            "default_auto_approve_supported": True,
            "full_access_supported": True,
            "per_tool_allow_supported": False,
            "local_cli_worker_supported": bool(codex_binary),
            "vscode_link_supported": bool(openai_path),
            "cloud_agent_supported": False,
            "supports_auto_approve": bool(codex_binary),
            "supports_defer_resume": False,
            "supported_models": [],
            "selected_model": None,
            "applied": applied,
            "config_valid": bool(config_health.get("valid", True)),
            "config_error": config_health.get("error"),
            "config_checks": config_health.get("checks") or {},
            "verified": "blocked" if not config_health.get("valid", True) else ("partial" if codex_installed else "unavailable"),
            "version": openai_version,
            "paths": {
                "extension": str(openai_path) if openai_path else None,
                "config": str(config_path_for_provider),
                "home": str(_codex_home(config, provider_id)),
                "binary": codex_binary,
            },
            "settings": {
                "orchestrator.ask_for_approval": profile.get("ask_for_approval", "never"),
                "orchestrator.sandbox_mode": profile.get("sandbox_mode", "workspace-write"),
                "dangerously_bypass": profile.get("dangerously_bypass", False),
                "codex.codex_home": profile.get("codex_home"),
            },
            "notes": notes,
        }

    report = {
        "generated_at": utc_now(),
        "workspace": {
            "root": str(ROOT),
            "code_cli": code_cli,
            "custom_agents": custom_agents,
            "extensions": _relevant_extensions(),
            "shared_state_files": {
                "status_file": str(config_path(config, "status_file")),
                "activity_log": str(config_path(config, "activity_log")),
                "current_work": str(config_path(config, "current_work")),
                "dashboard": str(config_path(config, "dashboard")),
            },
        },
        "agent_adapters": {
            agent_id: build_adapter(agent.get("adapter", "file_inbox"), config=config, provider_capabilities={})
            .capability(agent_id)
            .as_dict()
            for agent_id, agent in config.get("agents", {}).items()
        },
        "providers": {
            **{
                provider_id: _claude_provider_report(
                    config,
                    provider_id=provider_id,
                    claude_path=claude_path,
                    claude_version=claude_version,
                    claude_local=claude_local,
                    claude_permissions=claude_permissions,
                    workspace_settings=workspace_settings,
                    claude_applied=claude_applied,
                )
                for provider_id in claude_provider_ids
            },
            **{
                provider_id: _gemini_provider_report(
                    config,
                    provider_id=provider_id,
                    gemini_path=gemini_path,
                    gemini_version=gemini_version,
                    workspace_settings=workspace_settings,
                    gemini_applied=gemini_applied if provider_id == "gemini" else True,
                )
                for provider_id in gemini_provider_ids
            },
            **{provider_id: codex_provider_report(provider_id) for provider_id in codex_provider_ids},
            "copilot": {
                "installed": copilot_installed,
                "host_layer": "CLI + VS Code extension + GitHub CLI"
                if copilot_binary and copilot_path and gh_binary
                else (
                    "CLI + VS Code extension"
                    if copilot_binary and copilot_path
                    else ("GitHub CLI + VS Code extension" if gh_binary and copilot_path else "VS Code extension")
                ),
                "delivery_mode": copilot_settings.get("delivery_mode", "copilot_local"),
                "approval_mode": "allow_all_tools" if copilot_settings.get("local", {}).get("allow_all_tools", False) else "per_tool_flags",
                "persistent_allow_supported": False,
                "default_auto_approve_supported": bool(copilot_binary and copilot_auth_ready),
                "full_access_supported": bool(copilot_binary and copilot_auth_ready),
                "per_tool_allow_supported": bool(copilot_binary and copilot_auth_ready),
                "local_cli_worker_supported": bool(copilot_binary and copilot_auth_ready),
                "vscode_link_supported": bool(copilot_path),
                "cloud_agent_supported": bool(gh_binary and gh_version and gh_version >= (2, 80, 0) and gh_auth_ready),
                "supports_auto_approve": bool(copilot_binary and copilot_auth_ready),
                "supports_defer_resume": False,
                "auth_ready": copilot_auth_ready,
                "supported_models": copilot_model_preference.get("supported", []),
                "selected_model": copilot_model_preference.get("default"),
                "applied": copilot_applied,
                "verified": "partial" if copilot_installed else "unavailable",
                "version": copilot_version,
                "paths": {
                    "extension": str(copilot_path) if copilot_path else None,
                    "copilot_binary": copilot_binary,
                    "gh_binary": gh_binary,
                    "workspace_settings": str(WORKSPACE_SETTINGS_PATH),
                },
                "settings": {
                    "github.copilot.chat.backgroundAgent.enabled": _workspace_setting(
                        workspace_settings, "github.copilot.chat.backgroundAgent.enabled"
                    ),
                    "github.copilot.chat.cloudAgent.enabled": _workspace_setting(
                        workspace_settings, "github.copilot.chat.cloudAgent.enabled"
                    ),
                    "github.copilot.chat.claudeAgent.enabled": _workspace_setting(
                        workspace_settings, "github.copilot.chat.claudeAgent.enabled"
                    ),
                    "local.allow_all_tools": copilot_settings.get("local", {}).get("allow_all_tools", False),
                    "cloud.follow": copilot_settings.get("cloud", {}).get("follow", False),
                },
                "notes": [
                    "The installed Copilot Chat extension exposes background-agent, cloud-agent, and Claude-agent sessions in VS Code.",
                    "Local worker automation requires the `copilot` CLI plus valid GitHub authentication; cloud delegation requires `gh >= 2.80` plus `gh auth status`.",
                    "The installed Copilot CLI exposes a verified `--model` flag, so Grok routing can be expressed as a Copilot model selection.",
                ],
            },
            "grok": {
                "installed": copilot_installed,
                "host_layer": "Copilot model selection",
                "delivery_mode": "copilot_local",
                "approval_mode": "inherits_copilot",
                "persistent_allow_supported": False,
                "default_auto_approve_supported": bool(copilot_binary and copilot_auth_ready),
                "full_access_supported": bool(copilot_binary and copilot_auth_ready),
                "per_tool_allow_supported": bool(copilot_binary and copilot_auth_ready),
                "local_cli_worker_supported": bool(copilot_binary and copilot_auth_ready),
                "vscode_link_supported": bool(copilot_path),
                "cloud_agent_supported": False,
                "supports_auto_approve": bool(copilot_binary and copilot_auth_ready),
                "supports_defer_resume": False,
                "auth_ready": copilot_auth_ready,
                "supported_models": [copilot_model_preference.get("grok")] if copilot_model_preference.get("grok") else [],
                "selected_model": copilot_model_preference.get("grok"),
                "applied": False,
                "verified": "partial" if copilot_installed else "unavailable",
                "version": copilot_version,
                "paths": {
                    "host_extension": str(copilot_path) if copilot_path else None,
                    "copilot_binary": copilot_binary,
                },
                "settings": {
                    "model_preference.grok": copilot_model_preference.get("grok"),
                },
                "notes": [
                    "Grok is treated as a Copilot model preference rather than a standalone provider.",
                    "The orchestrator uses the verified Copilot CLI `--model` flag to request `grok-code-fast-1` when the Grok target is selected.",
                ],
            },
        },
    }
    return report


def write_provider_capabilities(config: dict[str, Any], report: dict[str, Any] | None = None) -> Path:
    report = report or provider_capabilities(config)
    target = config_path(config, "provider_capabilities")
    write_json(target, report)
    return target


def desired_sync_state(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        "workspace_settings": desired_workspace_settings(config),
        "claude_local_settings": desired_claude_local_settings(config, current=_claude_local_settings()),
        "gemini_settings": desired_gemini_settings(config),
    }


def apply_workspace_settings(config: dict[str, Any]) -> dict[str, Any]:
    settings = _workspace_settings()
    updated = {**settings, **desired_workspace_settings(config)}
    write_json(WORKSPACE_SETTINGS_PATH, updated)
    return updated


def apply_claude_local_settings(config: dict[str, Any]) -> dict[str, Any]:
    updated = desired_claude_local_settings(config, current=_claude_local_settings())
    write_json(CLAUDE_LOCAL_SETTINGS_PATH, updated)
    return updated


def apply_gemini_settings(config: dict[str, Any]) -> dict[str, Any]:
    current = _gemini_settings()
    desired = desired_gemini_settings(config)
    merged_security = {**current.get("security", {}), **desired.get("security", {})}
    if desired.get("security", {}).get("auth"):
        merged_security["auth"] = {
            **current.get("security", {}).get("auth", {}),
            **desired["security"]["auth"],
        }
    updated = {
        "general": {**current.get("general", {}), **desired.get("general", {})},
        "security": merged_security,
    }
    if desired.get("model"):
        updated["model"] = {**current.get("model", {}), **desired["model"]}
    GEMINI_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    write_json(GEMINI_SETTINGS_PATH, updated)
    return updated


def backup_targets(config: dict[str, Any]) -> list[Path]:
    return [WORKSPACE_SETTINGS_PATH, CLAUDE_LOCAL_SETTINGS_PATH, GEMINI_SETTINGS_PATH]


def latest_backup_dir() -> Path | None:
    backups_dir = ROOT / ".orchestrator" / "backups"
    if not backups_dir.exists():
        return None
    candidates = [path for path in backups_dir.iterdir() if path.is_dir()]
    if not candidates:
        return None
    return sorted(candidates)[-1]


def write_backup_manifest(backup_dir: Path, manifest: dict[str, Any]) -> None:
    write_json(backup_dir / "manifest.json", manifest)


def load_backup_manifest(backup_dir: Path) -> dict[str, Any]:
    return load_json(backup_dir / "manifest.json", default={}) or {}


def create_backup(config: dict[str, Any]) -> Path:
    backup_dir = ROOT / ".orchestrator" / "backups" / utc_now().replace(":", "").replace("-", "")
    backup_dir.mkdir(parents=True, exist_ok=True)
    manifest = {"created_at": utc_now(), "files": []}
    for index, target in enumerate(backup_targets(config), start=1):
        entry = {"target_path": str(target), "existed": target.exists(), "backup_file": None}
        if target.exists():
            backup_name = f"{index:02d}-{target.name}"
            shutil.copy2(target, backup_dir / backup_name)
            entry["backup_file"] = backup_name
        manifest["files"].append(entry)
    write_backup_manifest(backup_dir, manifest)
    return backup_dir


def restore_backup(backup_dir: Path) -> list[str]:
    manifest = load_backup_manifest(backup_dir)
    restored: list[str] = []
    for entry in manifest.get("files", []):
        target = Path(entry["target_path"])
        if entry.get("existed"):
            backup_file = backup_dir / entry["backup_file"]
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup_file, target)
            restored.append(str(target))
        elif target.exists():
            target.unlink()
            restored.append(str(target))
    return restored


def main() -> int:
    config = load_config()
    path = write_provider_capabilities(config)
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
