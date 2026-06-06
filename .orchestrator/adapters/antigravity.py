from __future__ import annotations

import os
from pathlib import Path

from adapters.base import BaseAdapter, DeliveryCapability, DeliveryRequest, DeliveryResult
from adapters.file_inbox import FileInboxAdapter
from common import (
    agent_config_for,
    command_exists,
    delivery_runtime_env,
    delivery_workspace_root,
    new_runtime_id,
    runtime_log_path,
    spawn_background_process,
    worker_runtime_paths,
)


# Antigravity CLI (`agy`) is the successor to the Gemini CLI; Google stops
# serving the legacy Gemini CLI for consumer tiers on 2026-06-18. The OAuth
# token lives under ~/.gemini/antigravity-cli/ (relative to HOME), separate
# from the legacy Gemini CLI oauth_creds.json.
ANTIGRAVITY_OAUTH_TOKEN_REL = Path(".gemini") / "antigravity-cli" / "antigravity-oauth-token"


def _provider_key(config: dict | None, agent_id: str | None = None, provider_id: str | None = None) -> str:
    if provider_id:
        return str(provider_id).strip() or "antigravity"
    if agent_id:
        agent = agent_config_for(config or {}, agent_id)
        return str(agent.get("provider") or agent.get("id") or agent_id).strip() or "antigravity"
    return "antigravity"


def _provider_settings(config: dict | None = None, provider_id: str | None = None) -> dict:
    providers = (config or {}).get("providers", {}) or {}
    key = _provider_key(config, provider_id=provider_id)
    return providers.get(key) or providers.get("antigravity") or {}


def _provider_env(config: dict | None = None, provider_id: str | None = None) -> dict[str, str]:
    provider = _provider_settings(config, provider_id)
    env: dict[str, str] = {}
    for block_name in ("runtime", "antigravity"):
        block = provider.get(block_name, {}) or {}
        for key, value in (block.get("env", {}) or {}).items():
            if value is None:
                continue
            env[str(key)] = os.path.expanduser(str(value))
    return env


def _antigravity_home(config: dict | None = None, provider_id: str | None = None) -> Path:
    provider = _provider_settings(config, provider_id)
    runtime = provider.get("antigravity", {})
    home = str(runtime.get("config_home") or runtime.get("home") or "").strip()
    return Path(os.path.expanduser(home)) if home else Path.home()


def _oauth_token_path(config: dict | None = None, provider_id: str | None = None) -> Path:
    return _antigravity_home(config, provider_id) / ANTIGRAVITY_OAUTH_TOKEN_REL


def _configured_cli(config: dict | None = None, provider_id: str | None = None) -> str | None:
    provider = _provider_settings(config, provider_id)
    runtime = provider.get("antigravity", {})
    return command_exists(runtime.get("cli") or "agy")


def _allow_inbox_fallback(config: dict | None = None, provider_id: str | None = None) -> bool:
    provider = _provider_settings(config, provider_id)
    return bool(provider.get("allow_inbox_fallback", True))


def _auth_ready(config: dict | None = None, provider_id: str | None = None) -> bool:
    env = {**os.environ, **_provider_env(config, provider_id)}
    if env.get("GEMINI_API_KEY"):
        return True
    return _oauth_token_path(config, provider_id).exists()


class AntigravityAdapter(BaseAdapter):
    name = "antigravity"

    def capability(self, agent_id: str) -> DeliveryCapability:
        provider_id = _provider_key(self.config, agent_id=agent_id)
        allow_inbox_fallback = _allow_inbox_fallback(self.config, provider_id)
        cli = _configured_cli(self.config, provider_id)
        auth_ready = _auth_ready(self.config, provider_id)
        supported = bool(cli and auth_ready)
        if cli and auth_ready:
            notes = "Uses the verified Antigravity CLI `agy --prompt`, local OAuth/API-key auth, and auto-approval mode."
        elif cli:
            notes = "Antigravity CLI (agy) is installed but not authenticated for non-interactive use."
        else:
            notes = "Antigravity CLI (agy) is not installed."
        if not supported and not allow_inbox_fallback:
            notes = f"{notes} Inbox fallback is disabled for this provider."
        return DeliveryCapability(
            adapter=self.name,
            supported=bool(cli),
            requires_manual_confirmation=bool(not supported and allow_inbox_fallback),
            can_auto_deliver=supported,
            can_auto_approve_edits=supported,
            delivery_mode="antigravity" if (supported or not allow_inbox_fallback) else "file_inbox",
            verified="verified" if supported else ("partial" if cli else "unavailable"),
            host="Antigravity CLI" if (cli or not allow_inbox_fallback) else "Antigravity CLI + inbox fallback",
            notes=notes,
        )

    def deliver(self, request: DeliveryRequest) -> DeliveryResult:
        provider_id = _provider_key(self.config, agent_id=request.agent_id, provider_id=request.provider)
        capability = self.capability(request.agent_id)
        if not capability.supported or not capability.can_auto_deliver:
            if not _allow_inbox_fallback(self.config, provider_id):
                reason = capability.notes or "Antigravity auto-delivery is unavailable and inbox fallback is disabled."
                return DeliveryResult(
                    ok=False,
                    adapter=self.name,
                    mode="antigravity",
                    target=agent_config_for(self.config, request.agent_id).get("display_name", request.agent_id),
                    auto_delivered=False,
                    manual_confirmation_required=False,
                    error=reason,
                    notes=reason,
                )
            fallback = FileInboxAdapter(config=self.config, provider_capabilities=self.provider_capabilities)
            result = fallback.deliver(request)
            result.adapter = self.name
            result.mode = "file_inbox"
            result.notes = f"{result.notes}. {capability.notes}"
            if not capability.supported:
                result.error = capability.notes
            return result

        provider = _provider_settings(self.config, provider_id)
        settings = provider.get("antigravity", {})
        approval = provider.get("approval", {})
        cli = _configured_cli(self.config, provider_id) or settings.get("cli") or "agy"
        agent_cfg = agent_config_for(self.config, request.agent_id)
        display_name = str(agent_cfg.get("display_name") or request.agent_id)
        workspace_root = delivery_workspace_root(self.config, request.metadata)

        command = [cli]
        model = str(settings.get("model") or "").strip()
        if model:
            command.extend(["--model", model])
        print_timeout = str(settings.get("print_timeout") or "").strip()
        if print_timeout:
            command.extend(["--print-timeout", print_timeout])
        # Auto-approve tool/edit permissions for non-interactive worker runs.
        if approval.get("dangerously_skip_permissions", True):
            command.append("--dangerously-skip-permissions")
        include_directories = settings.get("include_directories")
        if include_directories:
            root = workspace_root
            paths = [str(root)] if include_directories is True else include_directories
            if isinstance(paths, (str, os.PathLike)):
                paths = [paths]
            for path in paths:
                expanded = Path(os.path.expanduser(str(path)))
                command.extend(["--add-dir", str(expanded if expanded.is_absolute() else root / expanded)])
        command.extend(["--prompt", request.message])

        spawn_env: dict[str, str] = dict(os.environ)
        spawn_env.update(delivery_runtime_env(self.config, request.metadata))
        spawn_env.update(_provider_env(self.config, provider_id))
        spawn_env["AI_NAME"] = display_name
        spawn_env["ORCH_AGENT_ID"] = request.agent_id
        spawn_env["ORCH_PROVIDER"] = provider_id
        home = _antigravity_home(self.config, provider_id)
        if home != Path.home():
            spawn_env["HOME"] = str(home)
        if request.task_id:
            spawn_env["ORCH_TASK_ID"] = request.task_id
        if request.reason:
            spawn_env["ORCH_REASON"] = request.reason

        run_id = new_runtime_id(provider_id)
        log_path = runtime_log_path(provider_id, request.agent_id)
        runtime_paths = worker_runtime_paths(self.config, run_id)
        process, _ = spawn_background_process(
            command,
            cwd=workspace_root,
            log_path=log_path,
            env=spawn_env,
            run_id=run_id,
            heartbeat_path=runtime_paths["heartbeat_path"],
            status_path=runtime_paths["status_path"],
        )

        return DeliveryResult(
            ok=True,
            adapter=self.name,
            mode="antigravity",
            target=display_name,
            auto_delivered=True,
            manual_confirmation_required=False,
            notes="Antigravity CLI wake-up started in the background.",
            command=command,
            log_path=str(log_path),
            pid=process.pid,
            run_id=run_id,
            metadata={
                "heartbeat_path": str(runtime_paths["heartbeat_path"]),
                "runner_status_path": str(runtime_paths["status_path"]),
            },
        )
