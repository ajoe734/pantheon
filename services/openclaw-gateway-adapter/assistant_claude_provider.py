"""Claude Code CLI provider for the OpenClaw gateway adapter.

Invokes ``claude -p`` inside the gateway container using the dedicated
service-user CLAUDE_CONFIG_DIR mount.  Output is normalized from stream-json
or plain text into a structured provider result.  Missing binary, missing auth
mount, and timeout all produce a degraded result rather than an exception so
the caller can apply a deterministic fallback.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Mapping

from assistant_credential_mounts import (
    AssistantCredentialMounts,
    DEFAULT_CLAUDE_CONTAINER_CONFIG_DIR,
)

_DEFAULT_TIMEOUT = int(os.getenv("ASSISTANT_CLAUDE_PROVIDER_TIMEOUT", "60"))
_BINARY_NAME = "claude"
_BINARY_ENV = "PANTHEON_ASSISTANT_CLAUDE_BIN"
_PROVIDER_NAME = "claude"


@dataclass(frozen=True)
class ClaudeProviderResult:
    status: str
    text: str
    raw_events: List[Dict[str, Any]] = field(default_factory=list)
    degraded_reason: Optional[str] = None
    exit_code: Optional[int] = None
    config_dir: str = ""

    def to_dict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "provider": _PROVIDER_NAME,
            "status": self.status,
            "text": self.text,
            "config_dir": self.config_dir,
        }
        if self.degraded_reason is not None:
            result["degraded_reason"] = self.degraded_reason
        if self.exit_code is not None:
            result["exit_code"] = self.exit_code
        if self.raw_events:
            result["raw_events"] = self.raw_events
        return result


class AssistantClaudeProvider:
    """Runs Claude Code CLI for the gateway adapter."""

    def __init__(
        self,
        environ: Mapping[str, str] | None = None,
        *,
        mounts: AssistantCredentialMounts | None = None,
    ) -> None:
        self._environ = dict(environ if environ is not None else os.environ)
        self._mounts = mounts or AssistantCredentialMounts(self._environ)

    def readiness(self, *, auth_probe: bool = False) -> dict[str, Any]:
        checked_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        binary = _resolve_binary()
        mount_validation = self._mounts.validate_mounts().get(_PROVIDER_NAME)
        
        base = {
            "provider": _PROVIDER_NAME,
            "provider_name": _PROVIDER_NAME,
            "runtime": "openclaw_gateway_cli_mount",
            "checked_at": checked_at,
            "binary_path": binary or os.getenv(_BINARY_ENV, _BINARY_NAME),
            "version": "unknown",
            "auth": "not_checked",
            "auth_status": "not_checked",
            "credential_mount": _mount_metadata(mount_validation),
            "mount_mode": getattr(mount_validation, "mount_mode", "unknown"),
            "last_refresh_check_time": None,
            "ready": False,
            "status": "degraded",
        }

        if not binary:
            return {**base, "degraded_reason": "claude_binary_not_found"}

        try:
            version = subprocess.check_output([binary, "--version"], stderr=subprocess.STDOUT).decode().strip()
            base["version"] = version
        except Exception as e:
            return {**base, "degraded_reason": f"claude_version_probe_failed: {e}"}

        if not mount_validation:
            return {**base, "degraded_reason": "claude_mount_not_configured"}
        if not mount_validation.ready:
            return {
                **base,
                "degraded_reason": f"claude_mount_{mount_validation.status}",
                "auth": "unavailable",
                "auth_status": "mount_unavailable",
            }

        if auth_probe:
            # Claude doesn't have a simple no-op auth probe command like codex, 
            # so we use a tiny prompt.
            result = self.invoke("Reply with: ok", timeout=30)
            base["last_refresh_check_time"] = checked_at
            if result.status == "ok":
                base["auth"] = "account_session"
                base["auth_status"] = "ready"
                base["ready"] = True
                base["status"] = "ready"
                base["degraded_reason"] = None
            else:
                base["auth"] = "unavailable"
                base["auth_status"] = "failed"
                base["degraded_reason"] = f"claude_auth_{result.degraded_reason}"
        else:
            base["auth"] = "account_session"
            base["auth_status"] = "not_checked"
            base["ready"] = True
            base["status"] = "ready"
            base["degraded_reason"] = None

        return base

    def invoke(
        self,
        prompt: str,
        *,
        mode: str = "user",
        context_pack: Optional[Dict[str, Any]] = None,
        timeout: int = _DEFAULT_TIMEOUT,
    ) -> ClaudeProviderResult:
        return invoke_claude(
            prompt,
            mode=mode,
            context_pack=context_pack,
            timeout=timeout,
            mounts=self._mounts,
        )


def _mount_metadata(validation: Any) -> dict[str, Any]:
    if validation is None:
        return {"ready": False, "status": "missing", "container_target": "claude_config"}
    return {
        "ready": bool(validation.ready),
        "status": validation.status,
        "host_source": validation.host_source,
        "container_target": validation.container_target,
        "mount_mode": validation.mount_mode,
        "owner_check": validation.owner_check,
    }


def _resolve_config_dir(mounts: AssistantCredentialMounts) -> str:
    """Return the container-side CLAUDE_CONFIG_DIR from the mount contract."""
    for contract in mounts._contracts():
        if contract.provider == "claude":
            return contract.container_path
    return DEFAULT_CLAUDE_CONTAINER_CONFIG_DIR


def _resolve_binary() -> Optional[str]:
    """Return the configured container-side Claude binary path, if usable."""
    configured = os.getenv(_BINARY_ENV, "").strip()
    if configured:
        if os.path.isabs(configured):
            return configured if os.path.isfile(configured) and os.access(configured, os.X_OK) else None
        return shutil.which(configured)
    return shutil.which(_BINARY_NAME)


def _normalize_output(raw: str) -> tuple[str, List[Dict[str, Any]]]:
    """Parse stream-json or plain text output into (text, events).

    ``claude -p`` emits newline-delimited JSON events when ``--output-format
    stream-json`` is supplied.  Each event has a ``type`` field; the final
    ``result`` event carries the assistant text.  If the output is not valid
    JSON, it is treated as plain text.
    """
    events: List[Dict[str, Any]] = []
    text_parts: List[str] = []

    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
            events.append(event)
            event_type = event.get("type", "")
            if event_type == "result":
                result_val = event.get("result", "")
                if isinstance(result_val, str):
                    text_parts.append(result_val)
            elif event_type == "assistant":
                content = event.get("message", {}).get("content", [])
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            text_parts.append(block.get("text", ""))
                elif isinstance(content, str):
                    text_parts.append(content)
        except (json.JSONDecodeError, ValueError):
            text_parts.append(line)

    return "\n".join(text_parts).strip(), events


def invoke_claude(
    prompt: str,
    *,
    mode: str = "user",
    context_pack: Optional[Dict[str, Any]] = None,
    timeout: int = _DEFAULT_TIMEOUT,
    mounts: Optional[AssistantCredentialMounts] = None,
    tool_policy: Optional[Dict[str, Any]] = None,
) -> ClaudeProviderResult:
    """Invoke the Claude Code CLI and return a normalized result.

    Degraded results are returned (not raised) for:
    - binary not found
    - missing or invalid auth mount
    - process timeout
    - non-zero exit with parseable output
    - malformed output that cannot be normalized

    ``tool_policy`` is reserved for future brokered tool-use enforcement; it
    is not forwarded as free shell flags.  The caller is responsible for
    ensuring only allowlisted operations reach this function.
    """
    if mounts is None:
        mounts = AssistantCredentialMounts()

    binary = _resolve_binary()
    if not binary:
        return ClaudeProviderResult(
            status="degraded",
            text="",
            degraded_reason="binary_not_found",
            config_dir="",
        )

    validations = mounts.validate_mounts()
    claude_validation = validations.get("claude")
    if claude_validation is None or not claude_validation.ready:
        reason = "auth_mount_missing"
        if claude_validation is not None:
            reason = f"auth_mount_{claude_validation.status}"
        return ClaudeProviderResult(
            status="degraded",
            text="",
            degraded_reason=reason,
            config_dir="",
        )

    config_dir = _resolve_config_dir(mounts)

    env = {**os.environ, "CLAUDE_CONFIG_DIR": config_dir}

    cmd = [
        binary,
        "-p",
        prompt,
        "--output-format",
        "stream-json",
        "--permission-mode",
        "plan",
    ]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return ClaudeProviderResult(
            status="degraded",
            text="",
            degraded_reason="timeout",
            config_dir=config_dir,
        )

    raw = proc.stdout.decode("utf-8", errors="replace")
    stderr_raw = proc.stderr.decode("utf-8", errors="replace").strip()

    try:
        text, events = _normalize_output(raw)
    except Exception:
        return ClaudeProviderResult(
            status="degraded",
            text="",
            raw_events=[],
            degraded_reason="malformed_output",
            exit_code=proc.returncode,
            config_dir=config_dir,
        )

    if proc.returncode != 0 and not text:
        degraded_reason = "non_zero_exit"
        if stderr_raw and ("auth" in stderr_raw.lower() or "login" in stderr_raw.lower()):
            degraded_reason = "auth_failure"
        return ClaudeProviderResult(
            status="degraded",
            text=text,
            raw_events=events,
            degraded_reason=degraded_reason,
            exit_code=proc.returncode,
            config_dir=config_dir,
        )

    return ClaudeProviderResult(
        status="ok",
        text=text,
        raw_events=events,
        exit_code=proc.returncode,
        config_dir=config_dir,
    )
