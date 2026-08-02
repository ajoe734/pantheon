#!/usr/bin/env python3
from __future__ import annotations

import json
import hashlib
import os
import re
import shutil
import stat
import subprocess
import threading

import model_rotation
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from common import (
    ROOT,
    apply_claude_oauth_token_file,
    claude_auth_ready,
    claude_credentials_path,
    command_exists,
    config_path,
    load_config,
    load_json,
    normalize_agent_id,
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
EXTENSIONS_DIR = Path.home() / ".vscode-server" / "extensions"
COPILOT_CONFIG_DIR = Path.home() / ".copilot"
COPILOT_CONFIG_PATH = COPILOT_CONFIG_DIR / "config.json"
ANTIGRAVITY_OAUTH_TOKEN_REL = Path(".gemini") / "antigravity-cli" / "antigravity-oauth-token"
AUTH_PROBE_DEFAULT_INTERVAL_SECONDS = 900
AUTH_PROBE_FAILED_INTERVAL_SECONDS = 60
AUTH_PROBE_DEFAULT_TIMEOUT_SECONDS = 45
AUTH_PROBE_PROMPT = "Reply exactly: OK"
AUTH_PROBE_EXPECTED_OUTPUT = "OK"
AUTH_ERROR_MAX_CHARS = 600
CODEX_INHERITED_SESSION_ENV = (
    "CODEX_THREAD_ID",
    "CODEX_SESSION_ID",
    "CODEX_CONVERSATION_ID",
    "CODEX_PARENT_THREAD_ID",
)
CODEX_AUTH_REVOKED_MARKERS = (
    "refresh-token-revoked",
    "refresh_token_revoked",
    "refresh token revoked",
    "refresh token has been revoked",
    "token has been revoked",
    "token revoked",
    "invalid_grant",
)
CODEX_AUTH_FAILURE_MARKERS = (
    "status: 401",
    "401 unauthorized",
    "unauthorized",
    "authentication_failed",
    "not authenticated",
    "auth failed",
    "invalid authentication credentials",
    "invalid api key",
)
CODEX_QUOTA_MARKERS = (
    "hit your usage limit",
    "hit your weekly limit",
    "hit your limit",
    "usage limit reached",
    "quota_reached",
    "credit balance is too low",
)
CODEX_MODELS_CACHE_SCHEMA_MARKERS = (
    "models_cache.json",
    "supports_reasoning_summaries",
    "missing field",
)
CODEX_MODELS_CACHE_FILENAME = "models_cache.json"
_CODEX_QUOTA_RESET_ISO_PATTERN = re.compile(
    r"(?P<timestamp>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2}))"
)
_CODEX_QUOTA_RESET_EPOCH_PATTERN = re.compile(
    r'"?resetsAt"?\s*[:=]\s*"?(?P<epoch>\d{10})"?',
    re.IGNORECASE,
)
_CODEX_CACHE_RECOVERY_LOCKS: dict[str, threading.Lock] = {}
_CODEX_CACHE_RECOVERY_LOCKS_GUARD = threading.Lock()


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


def _codex_runtime_env(config: dict[str, Any] | None = None, provider_id: str = "codex") -> dict[str, str]:
    base_env = dict(os.environ)
    env = dict(base_env)
    provider = (config or {}).get("providers", {}).get(provider_id, {}) or {}
    for block_name in ("runtime", "codex"):
        block = provider.get(block_name, {}) or {}
        for key, value in (block.get("env", {}) or {}).items():
            if value is None:
                continue
            env[str(key)] = os.path.expanduser(str(value))
    for key in CODEX_INHERITED_SESSION_ENV:
        env.pop(key, None)

    codex_settings = provider.get("codex", {}) or {}
    api_key_env = str(codex_settings.get("api_key_env") or "").strip()
    if api_key_env:
        api_key_value = env.get(api_key_env) or base_env.get(api_key_env) or ""
        if api_key_value:
            env["OPENAI_API_KEY"] = api_key_value
    else:
        env.pop("OPENAI_API_KEY", None)
    codex_home = str(codex_settings.get("codex_home") or codex_settings.get("config_home") or "").strip()
    if codex_home:
        env["CODEX_HOME"] = os.path.expanduser(codex_home)
    return env


def _codex_runtime_env_with_overrides(
    config: dict[str, Any] | None = None,
    provider_id: str = "codex",
    env: dict[str, str] | None = None,
) -> dict[str, str]:
    runtime_env = _codex_runtime_env(config, provider_id)
    if env:
        for key, value in env.items():
            if value is None:
                continue
            runtime_env[str(key)] = str(value)
        for key in CODEX_INHERITED_SESSION_ENV:
            runtime_env.pop(key, None)
        codex_settings = ((config or {}).get("providers", {}).get(provider_id, {}) or {}).get("codex", {}) or {}
        api_key_env = str(codex_settings.get("api_key_env") or "").strip()
        if api_key_env and runtime_env.get(api_key_env):
            runtime_env["OPENAI_API_KEY"] = runtime_env[api_key_env]
    return runtime_env


def _antigravity_home(config: dict[str, Any] | None = None, provider_id: str = "antigravity") -> Path:
    provider = ((config or {}).get("providers", {}).get(provider_id, {}) or {}).get("antigravity", {}) or {}
    home = str(provider.get("config_home") or provider.get("home") or "").strip()
    return Path(os.path.expanduser(home)) if home else Path.home()


def _antigravity_oauth_token_path(config: dict[str, Any] | None = None, provider_id: str = "antigravity") -> Path:
    return _antigravity_home(config, provider_id) / ANTIGRAVITY_OAUTH_TOKEN_REL


def _antigravity_runtime_env(config: dict[str, Any] | None = None, provider_id: str = "antigravity") -> dict[str, str]:
    env = dict(os.environ)
    provider = (config or {}).get("providers", {}).get(provider_id, {}) or {}
    for block_name in ("runtime", "antigravity"):
        block = provider.get(block_name, {}) or {}
        for key, value in (block.get("env", {}) or {}).items():
            if value is None:
                continue
            env[str(key)] = os.path.expanduser(str(value))
    home = _antigravity_home(config, provider_id)
    if home != Path.home():
        env["ANTIGRAVITY_HOME"] = str(home)
        env["HOME"] = str(home)
    return env


def _codex_home(config: dict[str, Any] | None = None, provider_id: str = "codex") -> Path:
    provider = ((config or {}).get("providers", {}).get(provider_id, {}) or {}).get("codex", {}) or {}
    home = str(provider.get("codex_home") or provider.get("config_home") or "").strip()
    return Path(os.path.expanduser(home)) if home else Path.home() / ".codex"


def _codex_config_path(config: dict[str, Any] | None = None, provider_id: str = "codex") -> Path:
    return _codex_home(config, provider_id) / "config.toml"


def _codex_auth_path(config: dict[str, Any] | None = None, provider_id: str = "codex") -> Path:
    return _codex_home(config, provider_id) / "auth.json"


def _gemini_settings(config: dict[str, Any] | None = None, provider_id: str = "gemini") -> dict[str, Any]:
    return load_json(_gemini_settings_path(config, provider_id), default={}) or {}


def _truthy_env(name: str, env: dict[str, str] | None = None) -> bool:
    source = env if env is not None else os.environ
    return source.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _auth_probe_settings(config: dict[str, Any] | None = None, provider_id: str | None = None) -> dict[str, Any]:
    config = config or {}
    settings = dict(config.get("provider_auth") or {})
    provider_settings = (config.get("providers", {}).get(provider_id or "", {}) or {}).get("auth_probe", {}) or {}
    settings.update(provider_settings)
    settings.setdefault("probe_interval_seconds", AUTH_PROBE_DEFAULT_INTERVAL_SECONDS)
    settings.setdefault("failed_probe_interval_seconds", AUTH_PROBE_FAILED_INTERVAL_SECONDS)
    settings.setdefault("probe_timeout_seconds", AUTH_PROBE_DEFAULT_TIMEOUT_SECONDS)
    settings.setdefault("probe_prompt", AUTH_PROBE_PROMPT)
    settings.setdefault("enabled", True)
    return settings


def _parse_auth_probe_time(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _previous_provider_auth_probe(config: dict[str, Any], provider_id: str) -> dict[str, Any]:
    try:
        previous_report = load_json(config_path(config, "provider_capabilities"), default={}) or {}
    except (KeyError, OSError, TypeError):
        return {}
    previous_provider = ((previous_report.get("providers") or {}).get(provider_id) or {})
    if not isinstance(previous_provider, dict):
        return {}
    probe = previous_provider.get("auth_probe")
    if isinstance(probe, dict) and probe:
        return dict(probe)
    if previous_provider.get("last_auth_probe_at"):
        return {
            "ready": previous_provider.get("auth_ready"),
            "error": previous_provider.get("auth_error"),
            "checked_at": previous_provider.get("last_auth_probe_at"),
            "method": previous_provider.get("auth_method"),
            "source": "legacy_provider_capabilities",
        }
    return {}


def _auth_probe_due(config: dict[str, Any], provider_id: str, previous: dict[str, Any]) -> bool:
    settings = _auth_probe_settings(config, provider_id)
    if not to_bool(settings.get("enabled", True)):
        return False
    if _truthy_env("PANTHEON_PROVIDER_AUTH_PROBE_FORCE"):
        return True
    checked_at = _parse_auth_probe_time(previous.get("checked_at") or previous.get("last_auth_probe_at"))
    if checked_at is None:
        return True
    interval_key = "failed_probe_interval_seconds" if previous.get("ready") is False else "probe_interval_seconds"
    try:
        interval_seconds = int(settings.get(interval_key, AUTH_PROBE_DEFAULT_INTERVAL_SECONDS))
    except (TypeError, ValueError):
        interval_seconds = AUTH_PROBE_DEFAULT_INTERVAL_SECONDS
    if interval_seconds <= 0:
        return True
    return (datetime.now(timezone.utc) - checked_at).total_seconds() >= interval_seconds


def provider_auth_probe_due(
    config: dict[str, Any],
    provider_id: str,
    previous: dict[str, Any] | None = None,
) -> bool:
    """Return whether one targeted provider probe may spend a live call.

    Supervisor recovery paths already have the capability row in memory.  Let
    them consult the same success/failure intervals as the provider probes
    before forcing an authoritative check, rather than calling ``force=True``
    on every supervisor tick.  Ambiguous/unsupported results are fail-closed
    recovery outcomes, so they use the shorter failed-probe interval too.
    """

    previous_probe = dict(previous or _previous_provider_auth_probe(config, provider_id))
    if previous_probe.get("ready") is not True:
        previous_probe["ready"] = False
    return _auth_probe_due(config, provider_id, previous_probe)


def _auth_probe_record(
    provider_id: str,
    kind: str,
    *,
    ready: bool,
    method: str,
    error: str | None = None,
    status: str | None = None,
    source: str = "live",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    checked_at = utc_now()
    record: dict[str, Any] = {
        "provider": provider_id,
        "kind": kind,
        "ready": bool(ready),
        "status": status or ("ready" if ready else "not_ready"),
        "method": method,
        "error": error,
        "checked_at": checked_at,
        "last_auth_probe_at": checked_at,
        "source": source,
    }
    if metadata:
        record["metadata"] = metadata
    return record


def _reuse_auth_probe(provider_id: str, kind: str, previous: dict[str, Any], *, method: str) -> dict[str, Any]:
    record = dict(previous)
    record.setdefault("provider", provider_id)
    record.setdefault("kind", kind)
    record.setdefault("method", method)
    record.setdefault("status", "ready" if record.get("ready") is True else "not_ready")
    record.setdefault("source", "cached")
    if record.get("checked_at") and not record.get("last_auth_probe_at"):
        record["last_auth_probe_at"] = record.get("checked_at")
    if record.get("last_auth_probe_at") and not record.get("checked_at"):
        record["checked_at"] = record.get("last_auth_probe_at")
    record["source"] = "cached"
    return record


def _compact_auth_error(output: str | None) -> str | None:
    text = " ".join(str(output or "").split())
    if not text:
        return None
    if len(text) > AUTH_ERROR_MAX_CHARS:
        return f"{text[:AUTH_ERROR_MAX_CHARS]}..."
    return text


def _contains_any_marker(text: str, markers: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in markers)


def _codex_models_cache_schema_incompatible(output: str | None) -> bool:
    """Match only the observed Codex models-cache schema break.

    A generic JSON or CLI parse failure is not enough to authorize a provider-
    home mutation.  Recovery requires all three parts of the v0.144.6 failure:
    the exact cache filename, the newly required field, and the missing-field
    parser diagnosis.
    """

    normalized = str(output or "").lower()
    return all(marker.lower() in normalized for marker in CODEX_MODELS_CACHE_SCHEMA_MARKERS)


def _codex_quota_reset_at(output: str | None) -> str | None:
    """Extract one sanitized UTC reset timestamp without retaining payloads."""

    text = str(output or "")
    match = _CODEX_QUOTA_RESET_ISO_PATTERN.search(text)
    if match:
        try:
            parsed = datetime.fromisoformat(match.group("timestamp").replace("Z", "+00:00"))
        except ValueError:
            parsed = None
        if parsed is not None and parsed.tzinfo is not None:
            return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    epoch_match = _CODEX_QUOTA_RESET_EPOCH_PATTERN.search(text)
    if epoch_match:
        try:
            parsed = datetime.fromtimestamp(int(epoch_match.group("epoch")), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
        return parsed.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return None


def _codex_cache_identity(path: Path) -> dict[str, Any] | None:
    try:
        file_stat = path.lstat()
    except OSError:
        return None
    return {
        "device": int(file_stat.st_dev),
        "inode": int(file_stat.st_ino),
        "mode": int(file_stat.st_mode),
        "uid": int(file_stat.st_uid),
        "links": int(file_stat.st_nlink),
        "size": int(file_stat.st_size),
        "mtime_ns": int(file_stat.st_mtime_ns),
    }


def _codex_cache_identity_matches(left: dict[str, Any] | None, right: dict[str, Any] | None) -> bool:
    if not isinstance(left, dict) or not isinstance(right, dict):
        return False
    return all(
        left.get(key) == right.get(key)
        for key in ("device", "inode", "mode", "uid", "links", "size", "mtime_ns")
    )


def _codex_models_cache_path(
    config: dict[str, Any],
    provider_id: str,
    env: dict[str, str],
) -> tuple[Path | None, str | None]:
    configured_home = _codex_home(config, provider_id).expanduser()
    effective_home = Path(str(env.get("CODEX_HOME") or configured_home)).expanduser()
    if not configured_home.is_absolute() or not effective_home.is_absolute():
        return None, "provider_home_not_absolute"
    configured_home = Path(os.path.abspath(configured_home))
    effective_home = Path(os.path.abspath(effective_home))
    if configured_home != effective_home:
        return None, "provider_home_ambiguous"
    try:
        home_stat = effective_home.lstat()
    except OSError:
        return None, "provider_home_unavailable"
    if effective_home.is_symlink() or not stat.S_ISDIR(home_stat.st_mode):
        return None, "provider_home_unsafe"
    if int(home_stat.st_uid) != os.geteuid():
        return None, "provider_home_unowned"
    return effective_home / CODEX_MODELS_CACHE_FILENAME, None


def _codex_cache_recovery_lock(path: Path) -> threading.Lock:
    key = str(path)
    with _CODEX_CACHE_RECOVERY_LOCKS_GUARD:
        return _CODEX_CACHE_RECOVERY_LOCKS.setdefault(key, threading.Lock())


def _quarantine_codex_models_cache(
    config: dict[str, Any],
    provider_id: str,
    env: dict[str, str],
    expected_identity: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, str | None]:
    """Atomically move one exact, owned provider cache aside.

    The function never opens auth material.  It hashes the already-identified
    cache through an O_NOFOLLOW descriptor, proves the pathname still names the
    same inode/mtime/size observed before the failing probe, then uses one
    same-directory atomic replace.  Any ambiguity stays fail closed.
    """

    cache_path, path_error = _codex_models_cache_path(config, provider_id, env)
    if cache_path is None:
        return None, path_error or "cache_path_ambiguous"
    with _codex_cache_recovery_lock(cache_path):
        current_identity = _codex_cache_identity(cache_path)
        if not _codex_cache_identity_matches(expected_identity, current_identity):
            return None, "cache_identity_changed"
        assert current_identity is not None
        if (
            cache_path.is_symlink()
            or not stat.S_ISREG(int(current_identity["mode"]))
            or int(current_identity["uid"]) != os.geteuid()
            or int(current_identity["links"]) != 1
        ):
            return None, "cache_path_unsafe_or_unowned"

        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(cache_path, flags)
        except OSError:
            return None, "cache_open_failed"
        digest = hashlib.sha256()
        try:
            opened_stat = os.fstat(descriptor)
            opened_identity = {
                "device": int(opened_stat.st_dev),
                "inode": int(opened_stat.st_ino),
                "mode": int(opened_stat.st_mode),
                "uid": int(opened_stat.st_uid),
                "links": int(opened_stat.st_nlink),
                "size": int(opened_stat.st_size),
                "mtime_ns": int(opened_stat.st_mtime_ns),
            }
            if not _codex_cache_identity_matches(current_identity, opened_identity):
                return None, "cache_descriptor_identity_changed"
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
            final_stat = os.fstat(descriptor)
            final_identity = {
                "device": int(final_stat.st_dev),
                "inode": int(final_stat.st_ino),
                "mode": int(final_stat.st_mode),
                "uid": int(final_stat.st_uid),
                "links": int(final_stat.st_nlink),
                "size": int(final_stat.st_size),
                "mtime_ns": int(final_stat.st_mtime_ns),
            }
            if not _codex_cache_identity_matches(opened_identity, final_identity):
                return None, "cache_changed_while_hashing"
        finally:
            os.close(descriptor)

        if not _codex_cache_identity_matches(final_identity, _codex_cache_identity(cache_path)):
            return None, "cache_path_changed_before_quarantine"
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        quarantine_path = cache_path.with_name(
            f"{cache_path.name}.quarantine.{stamp}.{digest.hexdigest()[:12]}.{final_identity['inode']}"
        )
        if quarantine_path.exists() or quarantine_path.is_symlink():
            return None, "quarantine_target_exists"
        try:
            os.replace(cache_path, quarantine_path)
        except OSError:
            return None, "cache_quarantine_failed"
        if not _codex_cache_identity_matches(final_identity, _codex_cache_identity(quarantine_path)):
            return None, "quarantine_identity_mismatch"
        return {
            "outcome": "quarantined",
            "provider": provider_id,
            "cache_path": str(cache_path),
            "quarantine_path": str(quarantine_path),
            "evidence": {
                "device": final_identity["device"],
                "inode": final_identity["inode"],
                "uid": final_identity["uid"],
                "size": final_identity["size"],
                "mtime_ns": final_identity["mtime_ns"],
                "sha256": digest.hexdigest(),
            },
        }, None


def _codex_probe_ready(
    returncode: int,
    stdout: str | None,
    stderr: str | None,
    *,
    expected_output: str | None = AUTH_PROBE_EXPECTED_OUTPUT,
) -> tuple[bool, str | None, str]:
    output = "\n".join(part for part in (stdout, stderr) if part)
    compact_error = _compact_auth_error(output)
    if _codex_models_cache_schema_incompatible(output):
        return (
            False,
            "Codex models cache is incompatible with the installed CLI schema.",
            "models_cache_incompatible",
        )
    if _contains_any_marker(output, CODEX_QUOTA_MARKERS):
        return False, "Codex usage limit reached.", "quota_reached"
    if _contains_any_marker(output, CODEX_AUTH_REVOKED_MARKERS):
        return False, compact_error or "Codex refresh token is revoked.", "refresh_token_revoked"
    if returncode != 0:
        status = "auth_failed" if _contains_any_marker(output, CODEX_AUTH_FAILURE_MARKERS) else f"exit_{returncode}"
        return False, compact_error, status
    if _contains_any_marker(output, CODEX_AUTH_FAILURE_MARKERS):
        return False, compact_error or "Codex authentication probe reported an auth failure.", "auth_failed"
    stripped_lines = [line.strip() for line in output.splitlines() if line.strip()]
    if not stripped_lines:
        return False, "Codex auth probe exited 0 but returned no output.", "empty_output"
    expected = str(expected_output or "").strip()
    if expected and expected not in stripped_lines:
        return False, compact_error or "Codex auth probe returned unexpected output.", "unexpected_output"
    return True, None, "ready"


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


def _codex_auth_metadata(config: dict[str, Any], provider_id: str, env: dict[str, str]) -> dict[str, Any]:
    auth_path = _codex_auth_path(config, provider_id)
    api_key_env = str(((config.get("providers", {}).get(provider_id, {}) or {}).get("codex", {}) or {}).get("api_key_env") or "").strip()
    metadata = {
        "auth_file": str(auth_path),
        "auth_file_exists": auth_path.exists(),
        "api_key_env": api_key_env or None,
        "api_key_env_present": bool(api_key_env and env.get("OPENAI_API_KEY")),
    }
    return metadata


def _codex_auth_probe(
    config: dict[str, Any],
    provider_id: str,
    binary: str | None,
    *,
    env: dict[str, str] | None = None,
    force: bool = False,
    recover_incompatible_models_cache: bool = False,
) -> dict[str, Any]:
    env = _codex_runtime_env_with_overrides(config, provider_id, env)
    metadata = _codex_auth_metadata(config, provider_id, env)
    if not binary:
        return _auth_probe_record(
            provider_id,
            "codex",
            ready=False,
            method="codex_exec",
            error="Codex CLI is not installed.",
            status="cli_missing",
            metadata=metadata,
        )
    if not metadata.get("api_key_env_present") and not metadata.get("auth_file_exists"):
        api_key_note = (
            f" Configured API key env {metadata.get('api_key_env')} is not present."
            if metadata.get("api_key_env")
            else ""
        )
        return _auth_probe_record(
            provider_id,
            "codex",
            ready=False,
            method="codex_auth_file",
            error=f"Codex auth.json is missing and no API key is available.{api_key_note}",
            status="auth_material_missing",
            metadata=metadata,
        )

    previous = _previous_provider_auth_probe(config, provider_id)
    method = "codex_exec_api_key" if metadata.get("api_key_env_present") else "codex_exec_oauth"
    if previous and not force and not _auth_probe_due(config, provider_id, previous):
        return _reuse_auth_probe(provider_id, "codex", previous, method=method)

    settings = _auth_probe_settings(config, provider_id)
    prompt = str(settings.get("probe_prompt") or AUTH_PROBE_PROMPT)
    expected_output = str(settings.get("probe_expected_output") or AUTH_PROBE_EXPECTED_OUTPUT)
    timeout = float(settings.get("probe_timeout_seconds") or AUTH_PROBE_DEFAULT_TIMEOUT_SECONDS)
    command = [
        binary,
        "exec",
        "-C",
        str(ROOT),
        "-c",
        'ask_for_approval="never"',
        "-s",
        "read-only",
        "--skip-git-repo-check",
        prompt,
    ]
    cache_identity_before_probe: dict[str, Any] | None = None
    if recover_incompatible_models_cache:
        cache_path, _cache_path_error = _codex_models_cache_path(config, provider_id, env)
        if cache_path is not None:
            cache_identity_before_probe = _codex_cache_identity(cache_path)
    try:
        result = run_command(command, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return _auth_probe_record(
            provider_id,
            "codex",
            ready=False,
            method=method,
            error=f"Codex auth probe timed out after {timeout:g}s.",
            status="probe_timeout",
            metadata=metadata,
        )
    except OSError as exc:
        return _auth_probe_record(
            provider_id,
            "codex",
            ready=False,
            method=method,
            error=f"{type(exc).__name__}: {exc}",
            status="probe_error",
            metadata=metadata,
        )
    ready, error, status = _codex_probe_ready(
        result.returncode,
        result.stdout,
        result.stderr,
        expected_output=expected_output,
    )
    cache_recovery: dict[str, Any] | None = None
    if status == "models_cache_incompatible" and recover_incompatible_models_cache:
        cache_recovery, recovery_error = _quarantine_codex_models_cache(
            config,
            provider_id,
            env,
            cache_identity_before_probe,
        )
        if cache_recovery is None:
            metadata["models_cache_recovery"] = {
                "outcome": "fail_closed",
                "reason": recovery_error or "cache_recovery_failed",
            }
            return _auth_probe_record(
                provider_id,
                "codex",
                ready=False,
                method=method,
                error="Codex models cache recovery was not safe to perform.",
                status="models_cache_recovery_failed",
                metadata=metadata,
            )
        metadata["models_cache_recovery"] = cache_recovery
        try:
            result = run_command(command, timeout=timeout, env=env)
        except subprocess.TimeoutExpired:
            return _auth_probe_record(
                provider_id,
                "codex",
                ready=False,
                method=method,
                error=f"Codex auth probe timed out after cache recovery and {timeout:g}s.",
                status="probe_timeout",
                metadata=metadata,
            )
        except OSError as exc:
            return _auth_probe_record(
                provider_id,
                "codex",
                ready=False,
                method=method,
                error=f"{type(exc).__name__}: {exc}",
                status="probe_error",
                metadata=metadata,
            )
        ready, error, status = _codex_probe_ready(
            result.returncode,
            result.stdout,
            result.stderr,
            expected_output=expected_output,
        )

    record = _auth_probe_record(
        provider_id,
        "codex",
        ready=ready,
        method=method,
        error=error,
        status=status,
        metadata=metadata,
    )
    if status == "quota_reached":
        reset_at = _codex_quota_reset_at(
            "\n".join(part for part in (result.stdout, result.stderr) if part)
        )
        if reset_at:
            record["quota_reset_at"] = reset_at
    return record


def codex_auth_ready(
    provider_id: str = "codex",
    env: dict[str, str] | None = None,
    *,
    config: dict[str, Any] | None = None,
    binary: str | None = None,
) -> bool:
    config = config or load_config()
    provider_binary = (
        binary
        or _configured_provider_binary(config, provider_id, "codex", "codex")
        or command_exists("codex")
    )
    probe = _codex_auth_probe(config, provider_id, provider_binary, env=env, force=True)
    return probe.get("ready") is True


def _claude_auth_probe(
    config: dict[str, Any],
    provider_id: str,
    binary: str | None,
    env: dict[str, str],
    *,
    force: bool = False,
) -> dict[str, Any]:
    metadata = {
        "credentials": str(claude_credentials_path(env)),
        "credentials_exists": claude_credentials_path(env).exists(),
        "home": env.get("HOME"),
        "claude_config_dir": env.get("CLAUDE_CONFIG_DIR"),
    }
    if not binary:
        return _auth_probe_record(
            provider_id,
            "claude",
            ready=False,
            method="claude_auth_status",
            error="Claude CLI is not installed.",
            status="cli_missing",
            metadata=metadata,
        )
    previous = _previous_provider_auth_probe(config, provider_id)
    if previous and not force and not _auth_probe_due(config, provider_id, previous):
        return _reuse_auth_probe(provider_id, "claude", previous, method="claude_auth_status_refresh")
    status_payload = _claude_auth_status_payload(config, provider_id, binary, env)
    account_identity = _claude_account_identity(status_payload)
    account_group = _claude_account_group(account_identity)
    if account_identity:
        metadata["account_identity"] = account_identity
    if account_group:
        metadata["account_group"] = account_group
    ready = claude_auth_ready(binary, env=env, refresh_if_needed=True)
    record = _auth_probe_record(
        provider_id,
        "claude",
        ready=ready,
        method="claude_auth_status_refresh",
        error=None if ready else "Claude CLI authentication is missing or OAuth refresh failed.",
        status="ready" if ready else "auth_not_ready",
        metadata=metadata,
    )
    if account_identity:
        record["account_identity"] = account_identity
    if account_group:
        record["account_group"] = account_group
    return record


def _claude_auth_status_payload(
    config: dict[str, Any],
    provider_id: str,
    binary: str,
    env: dict[str, str],
) -> dict[str, Any]:
    settings = _auth_probe_settings(config, provider_id)
    timeout = float(settings.get("probe_timeout_seconds") or AUTH_PROBE_DEFAULT_TIMEOUT_SECONDS)
    try:
        result = run_command([binary, "auth", "status"], timeout=timeout, env=env)
    except (OSError, subprocess.TimeoutExpired):
        return {}
    if result.returncode != 0 or not result.stdout:
        return {}
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _first_nested_value(payload: dict[str, Any], *keys: str) -> str | None:
    queue: list[dict[str, Any]] = [payload]
    seen: set[int] = set()
    while queue:
        current = queue.pop(0)
        if id(current) in seen:
            continue
        seen.add(id(current))
        for key in keys:
            value = current.get(key)
            if value not in (None, "", [], {}):
                return str(value).strip()
        for value in current.values():
            if isinstance(value, dict):
                queue.append(value)
    return None


def _claude_account_identity(payload: dict[str, Any]) -> dict[str, Any]:
    if not payload or payload.get("loggedIn") is False:
        return {}
    identity = {
        "provider": "claude",
        "email": _first_nested_value(payload, "email", "userEmail", "accountEmail", "username"),
        "org_id": _first_nested_value(
            payload,
            "orgId",
            "organizationId",
            "organizationUUID",
            "organizationUuid",
            "orgUUID",
            "orgUuid",
        ),
        "org_name": _first_nested_value(payload, "orgName", "organizationName"),
        "subscription_type": _first_nested_value(payload, "subscriptionType", "subscription", "plan", "tier"),
    }
    return {key: value for key, value in identity.items() if value}


def _claude_account_group(identity: dict[str, Any]) -> str | None:
    account_key = str(identity.get("org_id") or identity.get("email") or "").strip().lower()
    if not account_key:
        return None
    digest = hashlib.sha256(f"claude:{account_key}".encode("utf-8")).hexdigest()[:16]
    return normalize_agent_id(f"claude_account_{digest}")


def _antigravity_auth_metadata(config: dict[str, Any], provider_id: str, env: dict[str, str]) -> dict[str, Any]:
    token_path = _antigravity_oauth_token_path(config, provider_id)
    return {
        "oauth_token": str(token_path),
        "oauth_token_exists": token_path.exists(),
        "home": str(_antigravity_home(config, provider_id)),
        "gemini_api_key_present": bool(env.get("GEMINI_API_KEY")),
    }


def _antigravity_credential_group(config: dict[str, Any], provider_id: str) -> str:
    """Identify the quota account behind one Antigravity provider alias.

    ``antigravity`` and ``antigravity1-1`` ... ``antigravity1-4`` all resolve to
    the same ``$HOME/.gemini/antigravity-cli`` OAuth token, so they are one
    account's quota rather than five independent worker lanes.  Group by the
    declared account/quota group when the config states one, otherwise by the
    resolved credential home plus OAuth token path.
    """
    provider_cfg = (config.get("providers", {}) or {}).get(provider_id, {}) or {}
    for key in ("account", "account_group", "quota_group"):
        declared = str(provider_cfg.get(key) or "").strip()
        if declared:
            return normalize_agent_id(f"antigravity_{declared}")
    home = str(_antigravity_home(config, provider_id))
    token = str(_antigravity_oauth_token_path(config, provider_id))
    digest = hashlib.sha256(f"antigravity:{home}\0{token}".encode("utf-8")).hexdigest()[:16]
    return normalize_agent_id(f"antigravity_account_{digest}")


def _share_auth_probe_across_credential_group(
    probe: dict[str, Any],
    provider_id: str,
    *,
    credential_group: str,
    shared_with: str,
) -> dict[str, Any]:
    """Reuse one credential group's probe result for a sibling alias.

    The aliases share a single OAuth token, so a second ``agy --prompt`` smoke
    proves nothing new and a quota/auth failure on one of them is a failure for
    every alias in the group.
    """
    record = dict(probe)
    record["provider"] = provider_id
    record["credential_group"] = credential_group
    record["shared_with"] = shared_with
    record["source"] = "shared_credential_group"
    return record


def _antigravity_probe_ready(returncode: int, stdout: str, combined: str) -> tuple[bool, str | None, str]:
    """Decide whether an `agy --prompt` smoke probe proves non-interactive auth.

    The Antigravity CLI exits 0 in print mode even when its OAuth token is
    revoked or expired: it simply emits no output, and the
    "You are not logged into Antigravity" notice only reaches the CLI's own
    log file (never the probe's stdout/stderr). A clean exit code therefore is
    not sufficient. Require a non-zero exit to fail, an exhausted-quota or
    not-logged-in marker to fail, and non-empty stdout before declaring ready.
    """
    lowered = combined.lower()
    # Quota exhaustion must be classified before the exit-code check: the CLI
    # exits 1 on a quota error, and "quota_reached" (a per-model condition the
    # rotation layer can route around) must not be reported as a generic
    # auth-down "exit_1".
    if "quota reached" in lowered or "individual quota" in lowered:
        return (
            False,
            "Antigravity account quota is exhausted; enable overages or wait for reset.",
            "quota_reached",
        )
    if returncode != 0:
        return False, _compact_auth_error(combined), f"exit_{returncode}"
    if "not logged into antigravity" in lowered or "not authenticated" in lowered:
        return (
            False,
            "Antigravity CLI is not logged in (silent print-mode failure).",
            "not_logged_in",
        )
    if not stdout:
        return (
            False,
            "Antigravity auth probe exited 0 but returned no output "
            "(silent not-logged-in print-mode failure).",
            "empty_output",
        )
    return True, None, "ready"


def _antigravity_auth_probe(
    config: dict[str, Any],
    provider_id: str,
    binary: str | None,
    *,
    force: bool = False,
) -> dict[str, Any]:
    env = _antigravity_runtime_env(config, provider_id)
    metadata = _antigravity_auth_metadata(config, provider_id, env)
    if not binary:
        return _auth_probe_record(
            provider_id,
            "antigravity",
            ready=False,
            method="agy_prompt",
            error="Antigravity CLI (agy) is not installed.",
            status="cli_missing",
            metadata=metadata,
        )
    if not metadata.get("gemini_api_key_present") and not metadata.get("oauth_token_exists"):
        return _auth_probe_record(
            provider_id,
            "antigravity",
            ready=False,
            method="antigravity_auth_material",
            error="Antigravity OAuth token is missing and GEMINI_API_KEY is not present.",
            status="auth_material_missing",
            metadata=metadata,
        )

    previous = _previous_provider_auth_probe(config, provider_id)
    method = "agy_prompt_api_key" if metadata.get("gemini_api_key_present") else "agy_prompt_oauth"
    if previous and not force and not _auth_probe_due(config, provider_id, previous):
        return _reuse_auth_probe(provider_id, "antigravity", previous, method=method)

    provider_settings = (config.get("providers", {}).get(provider_id, {}) or {}).get("antigravity", {}) or {}
    settings = _auth_probe_settings(config, provider_id)
    prompt = str(settings.get("probe_prompt") or AUTH_PROBE_PROMPT)
    timeout = float(settings.get("probe_timeout_seconds") or AUTH_PROBE_DEFAULT_TIMEOUT_SECONDS)
    print_timeout = str(settings.get("print_timeout") or provider_settings.get("probe_print_timeout") or "90s").strip()

    # The probe must exercise the same model the dispatch adapter would pick:
    # auth (the OAuth token) and per-model-family quota are separate failure
    # domains, and Gemini quota exhaustion must not report the account as
    # auth-down while the rotation fallback model still has quota.
    rotation = model_rotation.rotation_settings(config, provider_id)
    rotation_slot: str | None = None
    model = str(provider_settings.get("model") or "").strip()
    if rotation.get("enabled"):
        rotation_slot = model_rotation.active_slot(config, provider_id)
        if rotation_slot is None:
            # Every rotation model is already cooling, so there is nothing left
            # to probe: re-probing the exhausted primary cannot succeed, and the
            # quota_reached path below would call cool_slot again and SHORTEN the
            # running cooldown to a fresh interval. Report not-ready without
            # spending a call; the next probe after a cooldown expires retries.
            return _auth_probe_record(
                provider_id,
                "antigravity",
                ready=False,
                method=method,
                error="Every Antigravity rotation model is cooling after quota exhaustion.",
                status="rotation_models_cooling",
                metadata={**metadata, "rotation_slot": None},
            )
        slot_model = model_rotation.model_for_slot(rotation, rotation_slot)
        if slot_model:
            model = slot_model

    def _probe_once(probe_model: str, slot: str | None) -> dict[str, Any]:
        probe_metadata = dict(metadata)
        if probe_model:
            probe_metadata["probe_model"] = probe_model
        if slot is not None:
            probe_metadata["rotation_slot"] = slot
        command = [binary]
        if probe_model:
            command.extend(["--model", probe_model])
        if print_timeout:
            command.extend(["--print-timeout", print_timeout])
        command.extend(["--prompt", prompt])
        try:
            result = run_command(command, timeout=timeout, env=env)
        except subprocess.TimeoutExpired:
            return _auth_probe_record(
                provider_id,
                "antigravity",
                ready=False,
                method=method,
                error=f"Antigravity auth probe timed out after {timeout:g}s.",
                status="probe_timeout",
                metadata=probe_metadata,
            )
        except OSError as exc:
            return _auth_probe_record(
                provider_id,
                "antigravity",
                ready=False,
                method=method,
                error=f"{type(exc).__name__}: {exc}",
                status="probe_error",
                metadata=probe_metadata,
            )
        output = "\n".join(part for part in (result.stdout, result.stderr) if part)
        ready, error, status = _antigravity_probe_ready(
            result.returncode, (result.stdout or "").strip(), output
        )
        return _auth_probe_record(
            provider_id,
            "antigravity",
            ready=ready,
            method=method,
            error=error,
            status=status,
            metadata=probe_metadata,
        )

    record = _probe_once(model, rotation_slot)
    if (
        record.get("status") == "quota_reached"
        and rotation.get("enabled")
        and rotation_slot is not None
    ):
        # The probed model is out of quota, not the account's auth. Cool that
        # slot so dispatch rotates too, and re-probe on the alternate model;
        # only when every rotation model is exhausted is the lane really down.
        model_rotation.cool_slot(config, provider_id, rotation_slot)
        alternate_slot = model_rotation.active_slot(config, provider_id)
        if alternate_slot is not None and alternate_slot != rotation_slot:
            alternate_model = model_rotation.model_for_slot(rotation, alternate_slot)
            record = _probe_once(alternate_model, alternate_slot)
    return record


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


def probe_provider_auth(
    config: dict[str, Any],
    provider_id: str,
    *,
    force: bool = True,
    recover_incompatible_models_cache: bool = False,
) -> dict[str, Any]:
    """Probe one concrete provider immediately before dispatch.

    The periodic capability report is useful fleet telemetry, but its cached
    Claude/Antigravity probes are not an authoritative launch gate.  This
    targeted entry point probes only the account about to own a worker and can
    force a fresh check without rebuilding every provider capability.

    Providers without a live CLI challenge still return a normalized material
    check.  An unknown delivery mode returns ``ready=None`` so new adapters are
    not accidentally disabled merely because this module does not know them.
    """
    provider_key = str(provider_id or "").strip()
    provider = (config.get("providers", {}).get(provider_key, {}) or {})
    delivery_mode = str(provider.get("delivery_mode") or provider_key).strip().lower()

    if delivery_mode == "codex":
        binary = _configured_provider_binary(config, provider_key, "codex", "codex")
        return _codex_auth_probe(
            config,
            provider_key,
            binary,
            force=force,
            recover_incompatible_models_cache=recover_incompatible_models_cache,
        )
    if delivery_mode == "claude_cli":
        binary = _configured_provider_binary(config, provider_key, "runtime", "claude")
        return _claude_auth_probe(
            config,
            provider_key,
            binary,
            _provider_runtime_env(config, provider_key),
            force=force,
        )
    if delivery_mode == "antigravity":
        binary = _configured_provider_binary(config, provider_key, "antigravity", "agy")
        return _antigravity_auth_probe(config, provider_key, binary, force=force)
    if delivery_mode == "gemini":
        settings = _gemini_settings(config, provider_key)
        oauth_creds_path = _gemini_oauth_creds_path(config, provider_key)
        env = _gemini_runtime_env(config, provider_key)
        ready = _gemini_auth_ready(settings, oauth_creds_path=oauth_creds_path, env=env)
        return _auth_probe_record(
            provider_key,
            "gemini",
            ready=ready,
            method="gemini_auth_material",
            error=None if ready else "Gemini CLI authentication material is not ready.",
            status="ready" if ready else "auth_material_missing",
        )
    if delivery_mode in {"copilot", "copilot_local"}:
        gh_binary = command_exists(provider.get("cloud", {}).get("cli") or "gh")
        ready = _copilot_auth_ready(gh_binary)
        return _auth_probe_record(
            provider_key,
            "copilot",
            ready=ready,
            method="copilot_auth_material",
            error=None if ready else "Copilot/GitHub authentication material is not ready.",
            status="ready" if ready else "auth_material_missing",
        )
    return {
        "provider": provider_key,
        "kind": delivery_mode or "unknown",
        "ready": None,
        "status": "unsupported_probe",
        "method": "unsupported",
        "error": None,
        "checked_at": utc_now(),
        "last_auth_probe_at": utc_now(),
        "source": "live",
    }


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
        "Bash(git clean *)",
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
    auth_probe = _claude_auth_probe(config, provider_id, provider_binary, runtime_env)
    provider_auth_ready = bool(auth_probe.get("ready"))
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
    report = {
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
        "auth_error": auth_probe.get("error"),
        "auth_method": auth_probe.get("method"),
        "last_auth_probe_at": auth_probe.get("last_auth_probe_at") or auth_probe.get("checked_at"),
        "auth_probe": auth_probe,
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
    if auth_probe.get("account_group"):
        report["account_group"] = auth_probe.get("account_group")
    if auth_probe.get("account_identity"):
        report["account_identity"] = auth_probe.get("account_identity")
    return report


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


def _antigravity_provider_report(
    config: dict[str, Any],
    *,
    provider_id: str,
    credential_group: str | None = None,
    shared_probe: dict[str, Any] | None = None,
    shared_probe_provider: str | None = None,
) -> dict[str, Any]:
    provider_config = (config.get("providers", {}).get(provider_id, {}) or {})
    antigravity_runtime = provider_config.get("antigravity", {}) or {}
    selected_model = str(antigravity_runtime.get("model") or "").strip() or None
    provider_binary = _configured_provider_binary(config, provider_id, "antigravity", "agy")
    group = credential_group or _antigravity_credential_group(config, provider_id)
    if shared_probe is not None:
        auth_probe = _share_auth_probe_across_credential_group(
            shared_probe,
            provider_id,
            credential_group=group,
            shared_with=str(shared_probe_provider or ""),
        )
    else:
        auth_probe = _antigravity_auth_probe(config, provider_id, provider_binary)
        auth_probe["credential_group"] = group
    auth_ready = bool(auth_probe.get("ready"))
    installed = bool(provider_binary)
    notes = [
        "Antigravity workers use the local `agy` CLI with the provider-specific HOME/profile.",
        "The auth watchdog verifies non-interactive auth with a low-frequency `agy --prompt` smoke probe.",
        "Aliases sharing one OAuth token/home are one quota account: they share a single probe result "
        "and are not independent schedulable capacity.",
    ]
    if provider_id != "antigravity":
        notes.append(f"Provider `{provider_id}` uses its configured Antigravity CLI home/env profile when provided.")
    return {
        "account_group": group,
        "credential_group": group,
        "quota_group": provider_config.get("quota_group"),
        "installed": installed,
        "host_layer": "CLI" if provider_binary else "unavailable",
        "delivery_mode": provider_config.get("delivery_mode", "antigravity"),
        "approval_mode": "dangerously_skip_permissions"
        if (provider_config.get("approval", {}) or {}).get("dangerously_skip_permissions", True)
        else "default",
        "persistent_allow_supported": False,
        "default_auto_approve_supported": True,
        "full_access_supported": True,
        "per_tool_allow_supported": False,
        "local_cli_worker_supported": bool(provider_binary and auth_ready),
        "vscode_link_supported": False,
        "cloud_agent_supported": False,
        "supports_auto_approve": bool(provider_binary and auth_ready),
        "supports_defer_resume": False,
        "supported_models": [selected_model] if selected_model else [],
        "selected_model": selected_model,
        "auth_ready": auth_ready,
        "auth_error": auth_probe.get("error"),
        "auth_method": auth_probe.get("method"),
        "last_auth_probe_at": auth_probe.get("last_auth_probe_at") or auth_probe.get("checked_at"),
        "auth_probe": auth_probe,
        "applied": True,
        "verified": "verified" if installed and auth_ready else ("partial" if installed else "unavailable"),
        "version": None,
        "paths": {
            "binary": provider_binary,
            "home": str(_antigravity_home(config, provider_id)),
            "oauth_token": str(_antigravity_oauth_token_path(config, provider_id))
            if _antigravity_oauth_token_path(config, provider_id).exists()
            else None,
        },
        "settings": {
            "antigravity.model": selected_model,
            "antigravity.print_timeout": antigravity_runtime.get("print_timeout"),
            "antigravity.include_directories": antigravity_runtime.get("include_directories"),
            "approval.dangerously_skip_permissions": (provider_config.get("approval", {}) or {}).get(
                "dangerously_skip_permissions", True
            ),
            "env.GEMINI_API_KEY": bool(_antigravity_runtime_env(config, provider_id).get("GEMINI_API_KEY")),
        },
        "notes": notes,
    }


def _antigravity_provider_reports(
    config: dict[str, Any],
    provider_ids: list[str],
) -> dict[str, dict[str, Any]]:
    """Build every Antigravity alias report with one probe per quota account.

    Probing each alias separately both burned N ``agy --prompt`` smokes per
    report and let a quota-dead account keep publishing N independently
    ``auth_ready`` lanes.  One probe per credential group fixes both: every
    alias in a failed group is reported unschedulable.
    """
    reports: dict[str, dict[str, Any]] = {}
    group_probes: dict[str, tuple[str, dict[str, Any]]] = {}
    for provider_id in provider_ids:
        group = _antigravity_credential_group(config, provider_id)
        shared = group_probes.get(group)
        report = _antigravity_provider_report(
            config,
            provider_id=provider_id,
            credential_group=group,
            shared_probe=shared[1] if shared else None,
            shared_probe_provider=shared[0] if shared else None,
        )
        if shared is None:
            group_probes[group] = (provider_id, report["auth_probe"])
        reports[provider_id] = report
    return reports


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
    antigravity_provider_ids = list(
        dict.fromkeys(
            [
                "antigravity",
                *[
                    provider_id
                    for provider_id, settings in (config.get("providers", {}) or {}).items()
                    if provider_id != "antigravity" and (settings or {}).get("delivery_mode") == "antigravity"
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
        provider_binary = _configured_provider_binary(config, provider_id, "codex", "codex") or codex_binary
        # Telemetry refresh, not a launch gate: honour provider_auth
        # probe_interval_seconds and reuse the recent probe. The supervisor runs
        # this report before every loop, so forcing it here re-ran `codex exec`
        # for every Codex alias on every tick. The authoritative pre-dispatch
        # check stays `probe_provider_auth(config, provider, force=True)`.
        auth_probe = _codex_auth_probe(config, provider_id, provider_binary)
        auth_ready = bool(auth_probe.get("ready"))
        installed = bool(openai_path or provider_binary)
        config_path_for_provider = _codex_config_path(config, provider_id)
        applied = (
            profile.get("ask_for_approval", "never") == "never"
            and profile.get("sandbox_mode", "workspace-write") == "workspace-write"
        )
        return {
            "installed": installed,
            "host_layer": "CLI + VS Code extension" if openai_path and provider_binary else ("CLI" if provider_binary else "VS Code extension"),
            "delivery_mode": "codex",
            "quota_group": provider_settings.get("quota_group"),
            "approval_mode": f"orchestrator:{profile.get('ask_for_approval', 'never')}",
            "persistent_allow_supported": False,
            "default_auto_approve_supported": True,
            "full_access_supported": True,
            "per_tool_allow_supported": False,
            "local_cli_worker_supported": bool(provider_binary and auth_ready),
            "vscode_link_supported": bool(openai_path),
            "cloud_agent_supported": False,
            "supports_auto_approve": bool(provider_binary and auth_ready),
            "supports_defer_resume": False,
            "supported_models": [],
            "selected_model": None,
            "auth_ready": auth_ready,
            "auth_error": auth_probe.get("error"),
            "auth_method": auth_probe.get("method"),
            "last_auth_probe_at": auth_probe.get("last_auth_probe_at") or auth_probe.get("checked_at"),
            "auth_probe": auth_probe,
            "applied": applied,
            "verified": "verified" if installed and auth_ready else ("partial" if installed else "unavailable"),
            "version": openai_version,
            "paths": {
                "extension": str(openai_path) if openai_path else None,
                "config": str(config_path_for_provider),
                "home": str(_codex_home(config, provider_id)),
                "auth": str(_codex_auth_path(config, provider_id)),
                "binary": provider_binary,
            },
            "settings": {
                "orchestrator.ask_for_approval": profile.get("ask_for_approval", "never"),
                "orchestrator.sandbox_mode": profile.get("sandbox_mode", "workspace-write"),
                "dangerously_bypass": profile.get("dangerously_bypass", False),
                "codex.codex_home": profile.get("codex_home"),
                "codex.api_key_env": profile.get("api_key_env"),
            },
            "notes": [
                "Verified CLI flags from the locally installed Codex CLI help output.",
                "No verified persistent approval config keys were found in local extension metadata, so auto-approve is applied per orchestrated run rather than globally.",
                "The auth watchdog verifies non-interactive auth with a low-frequency read-only `codex exec` smoke probe.",
            ],
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
            **_antigravity_provider_reports(config, antigravity_provider_ids),
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
