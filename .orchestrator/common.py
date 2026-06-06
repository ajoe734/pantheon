#!/usr/bin/env python3
from __future__ import annotations

import gzip
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
import hashlib
import urllib.error
import urllib.request
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from task_archive import TaskResolver

ROOT = Path(__file__).resolve().parents[1]
ORCHESTRATOR_DIR = ROOT / ".orchestrator"
TASK_BRIEFS_DIR = ORCHESTRATOR_DIR / "task-briefs"
EVIDENCE_DIR = ORCHESTRATOR_DIR / "evidence"
CLOSEOUT_SPEC_PATH = ORCHESTRATOR_DIR / "skills" / "task-closeout-finalization.md"
WORKER_ANCHOR_SPEC_PATH = ORCHESTRATOR_DIR / "skills" / "worker-anchor-commit.md"
DEFAULT_CONFIG_PATH = ORCHESTRATOR_DIR / "config.json"
LOCAL_CONFIG_PATH = ORCHESTRATOR_DIR / "config.local.json"
PLANNING_STATE_PATH = ORCHESTRATOR_DIR / "planning-state.json"
DEFAULT_PLANNING_SHARED_FILES = [
    ROOT / "docs" / "02-architecture" / "consensus" / "phase1" / "README.md",
    ROOT / "docs" / "02-architecture" / "consensus" / "phase1" / "planning-session.json",
    ROOT / "docs" / "02-architecture" / "consensus" / "phase1" / "pantheon-backend-completion-checklist.md",
]
CLAUDE_OAUTH_TOKEN_URL = "https://platform.claude.com/v1/oauth/token"
CLAUDE_OAUTH_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
CLAUDE_OAUTH_SCOPES = (
    "user:profile",
    "user:inference",
    "user:sessions:claude_code",
    "user:mcp_servers",
    "user:file_upload",
)
CLAUDE_OAUTH_REFRESH_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "Origin": "https://claude.ai",
    "Referer": "https://claude.ai/",
    "User-Agent": "claude-code/2.1.117",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def load_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return deepcopy(default)
    last_error: json.JSONDecodeError | None = None
    for attempt in range(10):
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            return deepcopy(default)
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            sanitized = re.sub(r"//.*?$", "", text, flags=re.MULTILINE)
            sanitized = re.sub(r"/\*.*?\*/", "", sanitized, flags=re.DOTALL)
            sanitized = re.sub(r",(\s*[}\]])", r"\1", sanitized)
            if sanitized != text:
                try:
                    return json.loads(sanitized)
                except json.JSONDecodeError as sanitized_exc:
                    last_error = sanitized_exc
            else:
                last_error = exc
            if attempt < 9:
                time.sleep(0.05 * (attempt + 1))
    if last_error is not None:
        raise last_error
    return deepcopy(default)


def write_json(path: Path, payload: Any) -> None:
    ensure_parent(path)
    serialized = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(serialized)
        handle.flush()
        os.fsync(handle.fileno())
        temp_path = Path(handle.name)
    os.replace(temp_path, path)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    last_error: json.JSONDecodeError | None = None
    for attempt in range(10):
        rows: list[dict[str, Any]] = []
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
            return rows
        except json.JSONDecodeError as exc:
            last_error = exc
            if attempt < 9:
                time.sleep(0.05 * (attempt + 1))
    if last_error is not None:
        raise last_error
    return []


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    ensure_parent(path)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def deep_merge(base: Any, overlay: Any) -> Any:
    if isinstance(base, dict) and isinstance(overlay, dict):
        merged = deepcopy(base)
        for key, value in overlay.items():
            if key in merged:
                merged[key] = deep_merge(merged[key], value)
            else:
                merged[key] = deepcopy(value)
        return merged
    if isinstance(base, list) and isinstance(overlay, list):
        return deepcopy(overlay)
    return deepcopy(overlay)


def resolve_path(value: str | Path | None) -> Path | None:
    if value is None:
        return None
    path = Path(value)
    if not path.is_absolute():
        path = ROOT / path
    return path


def relpath(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def evidence_dir(config: dict[str, Any]) -> Path:
    configured = config.get("paths", {}).get("evidence_dir")
    path = resolve_path(configured) if configured else EVIDENCE_DIR
    if path is None:
        return EVIDENCE_DIR
    return path


def load_config(config_path: str | Path | None = None) -> dict[str, Any]:
    config_file = resolve_path(config_path) if config_path else DEFAULT_CONFIG_PATH
    if config_file is None:
        raise RuntimeError("Unable to resolve orchestrator config path")
    config = load_json(config_file, default={})
    if LOCAL_CONFIG_PATH.exists():
        config = deep_merge(config, load_json(LOCAL_CONFIG_PATH, default={}))
    return config


def config_path(config: dict[str, Any], key: str, default: str | None = None) -> Path:
    value = config.get("paths", {}).get(key, default)
    path = resolve_path(value)
    if path is None:
        raise KeyError(f"Missing config path for {key}")
    return path


def repo_root_for_config(config: dict[str, Any]) -> Path:
    return config_path(config, "status_file").parents[0]


def _expand_workspace_path(value: Any, *, base: Path) -> Path:
    path = Path(os.path.expanduser(str(value)))
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def delivery_workspace_root(config: dict[str, Any], metadata: dict[str, Any] | None = None) -> Path:
    repo_root = repo_root_for_config(config)
    raw_path = (metadata or {}).get("workspace_path")
    if raw_path:
        return _expand_workspace_path(raw_path, base=repo_root)
    return repo_root


def delivery_status_root(config: dict[str, Any], metadata: dict[str, Any] | None = None) -> Path:
    repo_root = repo_root_for_config(config)
    raw_path = (metadata or {}).get("status_root")
    if raw_path:
        return _expand_workspace_path(raw_path, base=repo_root)
    return repo_root


def delivery_runtime_env(config: dict[str, Any], metadata: dict[str, Any] | None = None) -> dict[str, str]:
    workspace_root = delivery_workspace_root(config, metadata)
    status_root = delivery_status_root(config, metadata)
    return {
        "PANTHEON_WORKTREE_ROOT": str(workspace_root),
        "PANTHEON_STATUS_ROOT": str(status_root),
        "ORCH_WORKSPACE_PATH": str(workspace_root),
    }


def github_cli_config_dir(env: Mapping[str, str] | None = None) -> Path:
    source = env or os.environ
    configured = str(source.get("GH_CONFIG_DIR") or "").strip()
    if configured:
        return Path(os.path.expanduser(configured))
    xdg_config_home = str(source.get("XDG_CONFIG_HOME") or "").strip()
    if xdg_config_home:
        return Path(os.path.expanduser(xdg_config_home)) / "gh"
    home = str(source.get("HOME") or str(Path.home())).strip() or str(Path.home())
    return Path(os.path.expanduser(home)) / ".config" / "gh"


def preserve_github_cli_auth_env(env: dict[str, str], source_env: Mapping[str, str] | None = None) -> None:
    if env.get("GH_CONFIG_DIR"):
        env["GH_CONFIG_DIR"] = os.path.expanduser(str(env["GH_CONFIG_DIR"]))
        return
    config_dir = github_cli_config_dir(source_env)
    if config_dir.exists():
        env["GH_CONFIG_DIR"] = str(config_dir)


def is_github_cli_auth_failure(reason: str | None) -> bool:
    normalized = compact_whitespace(reason).lower()
    if not normalized:
        return False
    markers = (
        "github cli is not authenticated",
        "gh cli is not authenticated",
        "gh is not authenticated",
        "you are not logged into any github hosts",
        "to log in, run: gh auth login",
    )
    return any(marker in normalized for marker in markers)


def run_command(
    command: list[str],
    *,
    cwd: Path | None = None,
    timeout: float | None = None,
    check: bool = False,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=str(cwd or ROOT),
        check=check,
        timeout=timeout,
        text=True,
        capture_output=True,
        env=env,
    )


def claude_credentials_path(env: dict[str, str] | None = None) -> Path:
    source = env or os.environ
    configured = str(source.get("CLAUDE_CONFIG_DIR") or "").strip()
    if configured:
        config_dir = Path(os.path.expanduser(configured))
    else:
        home = str(source.get("HOME") or str(Path.home())).strip() or str(Path.home())
        config_dir = Path(os.path.expanduser(home)) / ".claude"
    return config_dir / ".credentials.json"


def load_claude_oauth_tokens(env: dict[str, str] | None = None) -> tuple[dict[str, Any], dict[str, Any], Path] | None:
    credentials_path = claude_credentials_path(env)
    payload = load_json(credentials_path, default={}) or {}
    oauth = payload.get("claudeAiOauth")
    if not isinstance(oauth, dict):
        return None
    return payload, oauth, credentials_path


def claude_oauth_token_expired(oauth: dict[str, Any], *, skew_seconds: int = 300) -> bool:
    if not oauth.get("accessToken"):
        return True
    expires_at = oauth.get("expiresAt")
    if expires_at in (None, ""):
        return False
    try:
        expires_at_ms = int(expires_at)
    except (TypeError, ValueError):
        return True
    return expires_at_ms <= int(time.time() * 1000) + (skew_seconds * 1000)


def claude_oauth_token_from_env(env: dict[str, str] | None = None) -> str | None:
    source = env or os.environ
    token = str(source.get("CLAUDE_CODE_OAUTH_TOKEN") or "").strip()
    return token if token.startswith("sk-ant-") else None


def apply_claude_oauth_token_file(env: dict[str, str], runtime: dict[str, Any]) -> dict[str, str]:
    if claude_oauth_token_from_env(env):
        return env
    token_file = str(runtime.get("oauth_token_file") or runtime.get("oauth_token_path") or "").strip()
    if not token_file:
        return env
    path = Path(os.path.expanduser(token_file))
    try:
        token = path.read_text(encoding="utf-8").strip()
    except OSError:
        return env
    if token:
        env["CLAUDE_CODE_OAUTH_TOKEN"] = token
    return env


def refresh_claude_oauth_tokens(env: dict[str, str] | None = None, *, timeout: float = 15.0) -> dict[str, Any] | None:
    loaded = load_claude_oauth_tokens(env)
    if not loaded:
        return None
    payload, oauth, credentials_path = loaded
    refresh_token = str(oauth.get("refreshToken") or "").strip()
    if not refresh_token:
        return None
    request_body = json.dumps(
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": CLAUDE_OAUTH_CLIENT_ID,
            "scope": " ".join(CLAUDE_OAUTH_SCOPES),
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        CLAUDE_OAUTH_TOKEN_URL,
        data=request_body,
        headers=CLAUDE_OAUTH_REFRESH_HEADERS,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response_payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return None

    updated = deepcopy(oauth)
    updated["accessToken"] = response_payload.get("access_token") or updated.get("accessToken") or ""
    updated["refreshToken"] = response_payload.get("refresh_token") or refresh_token
    expires_in = response_payload.get("expires_in")
    if expires_in is not None:
        try:
            updated["expiresAt"] = int(time.time() * 1000) + (int(expires_in) * 1000)
        except (TypeError, ValueError):
            pass
    scopes = response_payload.get("scope")
    if isinstance(scopes, str) and scopes.strip():
        updated["scopes"] = scopes.split()
    elif not updated.get("scopes"):
        updated["scopes"] = list(CLAUDE_OAUTH_SCOPES)
    payload["claudeAiOauth"] = updated
    write_json(credentials_path, payload)
    return updated


def claude_auth_ready(binary: str | None, *, env: dict[str, str] | None = None, refresh_if_needed: bool = True) -> bool:
    if not binary:
        return False
    env_token = claude_oauth_token_from_env(env)
    if env_token:
        loaded = load_claude_oauth_tokens(env)
        if not loaded:
            return True
        _, oauth, _ = loaded
        stored_token = str(oauth.get("accessToken") or "").strip()
        if stored_token and stored_token != env_token:
            if not claude_oauth_token_expired(oauth):
                if env is not None:
                    env["CLAUDE_CODE_OAUTH_TOKEN"] = stored_token
            return True
        if stored_token and stored_token == env_token and not claude_oauth_token_expired(oauth):
            return True
        if not refresh_if_needed:
            return False
        refreshed = refresh_claude_oauth_tokens(env)
        if refreshed and not claude_oauth_token_expired(refreshed, skew_seconds=0):
            refreshed_token = str(refreshed.get("accessToken") or "").strip()
            if refreshed_token.startswith("sk-ant-") and env is not None:
                env["CLAUDE_CODE_OAUTH_TOKEN"] = refreshed_token
            return True
        return False
    status = run_command([binary, "auth", "status"], env=env)
    if status.returncode != 0 or not status.stdout:
        return False
    try:
        payload = json.loads(status.stdout)
    except json.JSONDecodeError:
        return False
    if not payload.get("loggedIn"):
        return False
    loaded = load_claude_oauth_tokens(env)
    if not loaded:
        return True
    _, oauth, _ = loaded
    if not claude_oauth_token_expired(oauth):
        return True
    if not refresh_if_needed:
        return False
    refreshed = refresh_claude_oauth_tokens(env)
    return bool(refreshed and not claude_oauth_token_expired(refreshed, skew_seconds=0))


def command_exists(name: str) -> str | None:
    return shutil.which(name)


def shell_quote(parts: list[str]) -> str:
    return " ".join(subprocess.list2cmdline([part]) if os.name == "nt" else __import__("shlex").quote(part) for part in parts)


def normalize_agent_id(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def display_name_for(config: dict[str, Any], agent_id: str) -> str:
    agent = config.get("agents", {}).get(normalize_agent_id(agent_id), {})
    return agent.get("display_name") or agent.get("name") or agent_id


def agent_config_for(config: dict[str, Any], agent_id: str) -> dict[str, Any]:
    normalized = normalize_agent_id(agent_id)
    agent = config.get("agents", {}).get(normalized)
    if agent:
        merged = deepcopy(agent)
        merged.setdefault("id", normalized)
        merged.setdefault("display_name", agent_id)
        return merged
    return {"id": normalized, "display_name": agent_id, "provider": normalized, "adapter": "file_inbox"}


def render_template(path: Path, variables: dict[str, Any]) -> str:
    text = path.read_text(encoding="utf-8")
    for key, value in variables.items():
        text = text.replace("{{" + key + "}}", str(value))
    return text


ACTIVITY_LOG_ROTATE_BYTES_DEFAULT = 50 * 1024 * 1024  # 50 MiB
ACTIVITY_LOG_ARCHIVE_SUBDIR = Path(".orchestrator") / "logs" / "activity-log-archive"


def _activity_log_rotate_threshold(config: dict[str, Any]) -> int:
    raw = (config.get("paths") or {}).get("activity_log_rotate_bytes")
    try:
        threshold = int(raw)
    except (TypeError, ValueError):
        return ACTIVITY_LOG_ROTATE_BYTES_DEFAULT
    return threshold if threshold > 0 else ACTIVITY_LOG_ROTATE_BYTES_DEFAULT


def _rotate_activity_log_if_needed(config: dict[str, Any], log_path: Path) -> None:
    try:
        size = log_path.stat().st_size
    except FileNotFoundError:
        return
    if size <= _activity_log_rotate_threshold(config):
        return
    archive_dir = ROOT / ACTIVITY_LOG_ARCHIVE_SUBDIR
    archive_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive_path = archive_dir / f"{log_path.stem}-{stamp}.jsonl.gz"
    counter = 1
    while archive_path.exists():
        archive_path = archive_dir / f"{log_path.stem}-{stamp}-{counter}.jsonl.gz"
        counter += 1
    rotating_path = log_path.with_suffix(log_path.suffix + ".rotating")
    try:
        os.replace(log_path, rotating_path)
    except FileNotFoundError:
        return
    try:
        with rotating_path.open("rb") as src, gzip.open(archive_path, "wb") as dst:
            shutil.copyfileobj(src, dst, length=1024 * 1024)
    finally:
        try:
            rotating_path.unlink()
        except FileNotFoundError:
            pass


def write_activity_log(config: dict[str, Any], entry: dict[str, Any]) -> None:
    payload = {
        "ts": utc_now(),
        "agent": "Orchestrator",
        **entry,
    }
    log_path = config_path(config, "activity_log")
    _rotate_activity_log_if_needed(config, log_path)
    append_jsonl(log_path, payload)


def runtime_log_path(prefix: str, target: str) -> Path:
    slug = normalize_agent_id(target) or "unknown"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    suffix = uuid.uuid4().hex[:6]
    return ORCHESTRATOR_DIR / "logs" / f"{stamp}-{prefix}-{slug}-{suffix}.log"


def new_runtime_id(prefix: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{prefix}-{stamp}-{uuid.uuid4().hex[:8]}"


def worker_runtime_paths(config: dict[str, Any], run_id: str) -> dict[str, Path]:
    safe_run_id = re.sub(r"[^a-zA-Z0-9_.-]+", "-", str(run_id or "worker")).strip("-") or "worker"
    try:
        root = config_path(config, "state_file").parent / "worker-runtime"
    except KeyError:
        try:
            root = config_path(config, "status_file").parent / ".orchestrator" / "worker-runtime"
        except KeyError:
            root = ORCHESTRATOR_DIR / "worker-runtime"
    return {
        "heartbeat_path": root / "heartbeats" / f"{safe_run_id}.json",
        "status_path": root / "status" / f"{safe_run_id}.json",
    }


def spawn_background_process(
    command: list[str],
    *,
    cwd: Path | None = None,
    log_path: Path,
    env: dict[str, str] | None = None,
    run_id: str | None = None,
    heartbeat_path: Path | None = None,
    status_path: Path | None = None,
    heartbeat_interval_seconds: int = 15,
    runner_enabled: bool = True,
) -> tuple[subprocess.Popen[str], Path]:
    ensure_parent(log_path)
    command_to_spawn = list(command)
    spawn_env = env
    if runner_enabled and run_id:
        if heartbeat_path is None:
            heartbeat_path = log_path.with_suffix(log_path.suffix + ".heartbeat.json")
        if status_path is None:
            status_path = log_path.with_suffix(log_path.suffix + ".status.json")
        ensure_parent(heartbeat_path)
        ensure_parent(status_path)
        spawn_env = dict(env or os.environ)
        spawn_env["ORCH_RUN_ID"] = str(run_id)
        spawn_env["ORCH_HEARTBEAT_PATH"] = str(heartbeat_path)
        spawn_env["ORCH_RUNNER_STATUS_PATH"] = str(status_path)
        command_to_spawn = [
            sys.executable,
            str(ORCHESTRATOR_DIR / "worker_runner.py"),
            "--run-id",
            str(run_id),
            "--heartbeat-path",
            str(heartbeat_path),
            "--status-path",
            str(status_path),
            "--heartbeat-interval-seconds",
            str(max(1, int(heartbeat_interval_seconds or 15))),
            "--",
            *command,
        ]
    handle = log_path.open("w", encoding="utf-8")
    process = subprocess.Popen(
        command_to_spawn,
        cwd=str(cwd or ROOT),
        stdout=handle,
        stderr=subprocess.STDOUT,
        text=True,
        env=spawn_env,
        start_new_session=True,
    )
    return process, log_path


def snapshot_task(task: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "id": task.get(schema["task_id_field"]),
        "status": task.get(schema["status_field"]),
        "owner": task.get(schema["assignee_field"]),
        "reviewer": task.get(schema["reviewer_field"]),
        "artifacts": list(task.get(schema.get("artifacts_field", "artifacts"), []) or []),
        "next": task.get(schema.get("next_field", "next")),
        "last_update": task.get(schema.get("last_update_field", "last_update")),
    }
    for key in (
        "task_class",
        "auto_generated",
        "helper_parent",
        "helper_kind",
        "mutates_canonical",
        "auto_created_by",
        "source_plane",
        "source_ref",
        "materialization_ref",
    ):
        if key in task:
            payload[key] = task.get(key)
    return payload


def load_status(config: dict[str, Any]) -> dict[str, Any]:
    return load_json(config_path(config, "status_file"), default={}) or {}


def planning_shared_files(planning_state: dict[str, Any] | None = None) -> list[Path]:
    state = planning_state if planning_state is not None else (load_json(PLANNING_STATE_PATH, default={}) or {})
    if str(state.get("status") or "") not in {"active", "human_required"}:
        return []

    files: list[Path] = []
    readme_path = resolve_path(((state.get("artifacts", {}) or {}).get("planning_readme", {}) or {}).get("path"))
    session_path = resolve_path(state.get("session_file"))
    for candidate in (readme_path, session_path):
        if candidate and candidate.exists():
            files.append(candidate)

    if not files:
        for path in DEFAULT_PLANNING_SHARED_FILES:
            if path.exists():
                files.append(path)

    unique: list[Path] = []
    seen: set[Path] = set()
    for path in files:
        if path in seen:
            continue
        seen.add(path)
        unique.append(path)
    return unique


def selected_shared_files(config: dict[str, Any]) -> list[Path]:
    files: list[Path] = []
    for key in ("status_file", "current_work", "activity_log", "dashboard"):
        path = config.get("paths", {}).get(key)
        if path:
            files.append(config_path(config, key))
    files.extend(planning_shared_files())
    return files


def serialize_shared_files(paths: list[Path]) -> str:
    return "\n".join(f"- {relpath(path)}" for path in paths)


def compact_whitespace(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def approval_tool_input_signature(tool_input: Any) -> str:
    try:
        payload = stable_json(tool_input if tool_input is not None else {})
    except TypeError:
        payload = compact_whitespace(tool_input)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def approval_tool_input_preview(tool_input: Any, *, limit: int = 220) -> str:
    if isinstance(tool_input, dict):
        for key in ("command", "cmd", "raw_command", "query", "path", "file", "url"):
            value = compact_whitespace(tool_input.get(key))
            if value:
                return value[:limit]
        preview = compact_whitespace(stable_json(tool_input))
        return preview[:limit]
    if isinstance(tool_input, list):
        preview = compact_whitespace(stable_json(tool_input))
        return preview[:limit]
    return compact_whitespace(tool_input)[:limit]


def unique_strings(items: list[str]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for item in items:
        value = str(item or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def summarize_failure_reason(reason: str | None, provider: str | None = None, *, limit: int = 180) -> dict[str, str]:
    raw = compact_whitespace(reason)
    provider_label = str(provider or "").strip() or "provider"
    if not raw:
        return {"kind": "unknown", "summary": f"{provider_label} failure", "detail": ""}

    lowered = raw.lower()
    if is_github_cli_auth_failure(raw):
        return {"kind": "tool_auth", "summary": "GitHub CLI auth unavailable", "detail": raw[: max(420, limit)]}
    if "you have no quota" in lowered:
        return {"kind": "quota", "summary": "402 You have no quota", "detail": raw[: max(420, limit)]}
    if "credit balance is too low" in lowered or "billing_error" in lowered:
        return {"kind": "quota", "summary": "Credit balance is too low", "detail": raw[: max(420, limit)]}
    if "free daily quota has been reached" in lowered:
        return {"kind": "quota", "summary": "Daily quota exceeded", "detail": raw[: max(420, limit)]}
    if "hit your usage limit" in lowered:
        return {"kind": "quota", "summary": "Codex usage limit reached", "detail": raw[: max(420, limit)]}
    if "hit your limit" in lowered:
        return {"kind": "quota", "summary": "Rate limit reached", "detail": raw[: max(420, limit)]}
    if "config.toml" in lowered and (
        "error loading" in lowered
        or "cannot be parsed" in lowered
        or "unsupported service_tier" in lowered
        or "unknown variant" in lowered
    ):
        return {"kind": "provider_config", "summary": "Provider config invalid", "detail": raw[: max(420, limit)]}
    if "rate limit" in lowered or "rate limited" in lowered or "capacity" in lowered or "quota exceeded" in lowered:
        return {"kind": "capacity", "summary": "Capacity / rate limit failure", "detail": raw[: max(420, limit)]}
    if "unauthorized" in lowered or "authentication" in lowered or "invalid api key" in lowered:
        return {"kind": "auth", "summary": "Authentication failure", "detail": raw[: max(420, limit)]}
    if "an unexpected critical error occurred" in lowered:
        return {"kind": "unknown_critical", "summary": "Unexpected critical provider failure", "detail": raw[: max(420, limit)]}
    return {"kind": "terminal", "summary": raw[:limit], "detail": raw[: max(420, limit)]}


def task_brief_path(task_id: str | None) -> Path:
    slug = normalize_agent_id(task_id or "unknown-task") or "unknown-task"
    return TASK_BRIEFS_DIR / f"{slug}.md"


def _recent_task_activity(config: dict[str, Any], task_id: str, *, limit: int = 6) -> list[dict[str, Any]]:
    path = config_path(config, "activity_log")
    if not path.exists():
        return []

    entries: list[dict[str, Any]] = []
    chunk_size = 64 * 1024
    max_scan_bytes = 16 * 1024 * 1024
    scanned = 0

    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        position = handle.tell()
        buffer = b""

        while position > 0 and len(entries) < limit and scanned < max_scan_bytes:
            read_size = min(chunk_size, position)
            position -= read_size
            handle.seek(position)
            chunk = handle.read(read_size)
            scanned += read_size
            buffer = chunk + buffer
            lines = buffer.splitlines()

            if position > 0:
                buffer = lines[0] if lines else buffer
                complete_lines = lines[1:]
            else:
                buffer = b""
                complete_lines = lines

            for raw_line in reversed(complete_lines):
                line = raw_line.decode("utf-8", errors="ignore").strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    # Ignore a partially-written tail line rather than stalling dispatch.
                    continue
                if str(entry.get("task_id") or "").strip() != task_id:
                    continue
                entries.append(entry)
                if len(entries) >= limit:
                    break

    entries.reverse()
    return entries


def write_task_brief(config: dict[str, Any], task_id: str | None) -> Path | None:
    if not task_id:
        return None
    status = load_status(config)
    tasks = status.get("tasks", []) or []
    task = next((item for item in tasks if str(item.get("id") or "").strip() == task_id), None)
    if task is None:
        return None
    resolver = TaskResolver(tasks)
    deps = [resolver.get(dep_id) for dep_id in (task.get("depends_on") or [])]
    deps = [item for item in deps if item]
    planning_state = load_json(PLANNING_STATE_PATH, default={}) or {}
    planning_active = str(planning_state.get("status") or "") in {"active", "human_required", "accepted"}
    source_ref = task.get("source_ref") if isinstance(task.get("source_ref"), dict) else {}
    source_plane = str(task.get("source_plane") or "").strip()
    recent = _recent_task_activity(config, task_id)
    path = task_brief_path(task_id)
    ensure_parent(path)
    body = [
        f"# Task Brief: {task_id}",
        "",
        "This file is generated by the orchestrator for task-scoped execution context.",
        "Treat `ai-status.json` as the durable execution source of truth only when you need to verify or update state.",
        "Do not read `current-work.md` by default for implementation context.",
        "",
        "## Task",
        f"- Title: {task.get('title') or '-'}",
        f"- Status: {task.get('status') or '-'}",
        f"- Owner: {task.get('owner') or '-'}",
        f"- Reviewer: {task.get('reviewer') or '-'}",
        f"- Phase: {task.get('phase') or '-'}",
        f"- Last update: {task.get('last_update') or '-'}",
        f"- Next: {compact_whitespace(task.get('next') or '-')}",
        "",
        "## Summary",
        f"{task.get('summary_zh') or '-'}",
        "",
        "## Dependencies",
    ]
    if deps:
        body.extend(
            f"- {dep.get('id')}: {resolver.dependency_status(dep.get('id'))} · {compact_whitespace(dep.get('title') or dep.get('summary_zh') or '-')}"
            for dep in deps
        )
    else:
        body.append("- none")
    body.extend(["", "## Artifacts"])
    artifacts = [str(item).strip() for item in (task.get("artifacts") or []) if str(item).strip()]
    body.extend([f"- {item}" for item in artifacts] or ["- none"])
    body.extend(["", "## Recent Task Activity"])
    if recent:
        body.extend(
            f"- {entry.get('ts') or '-'} · {entry.get('agent') or '-'} · {entry.get('type') or '-'} · {compact_whitespace(entry.get('message') or '-')}"
            for entry in recent
        )
    else:
        body.append("- none")
    body.extend(["", "## Relevant Canonical Files", "- AI_COLLABORATION_GUIDE.md", "- ai-status.json"])
    if planning_active:
        session_file = str(planning_state.get("session_file") or "").strip()
        if session_file:
            body.append(f"- {session_file}")
        else:
            fallback_planning_files = planning_shared_files(planning_state)
            if fallback_planning_files:
                body.append(f"- {relpath(fallback_planning_files[0])}")
    if source_plane or source_ref:
        body.extend(["", "## Planning Origin"])
        body.append(f"- Source plane: {source_plane or '-'}")
        if source_ref:
            for label, key in (
                ("Session", "session_id"),
                ("Phase", "phase"),
                ("Profile", "profile"),
                ("Planning dir", "planning_dir"),
                ("Session file", "session_file"),
                ("Consensus packet", "consensus_packet"),
                ("Execution materialization", "execution_materialization"),
            ):
                value = str(source_ref.get(key) or "").strip()
                if value:
                    body.append(f"- {label}: {value}")
    body.extend([f"- {item}" for item in artifacts[:6] if item not in {"AI_COLLABORATION_GUIDE.md", "ai-status.json"}])
    body.extend(
        [
            "",
            "## Working Rules",
            "- Use scripts/ai-status.sh or python3 scripts/ai_status.py for status changes.",
            "- Keep execution updates short and structured.",
            "- If you need raw provider/debug details, ask for the relevant runtime log or evidence ref instead of scanning global summaries.",
            "",
        ]
    )
    path.write_text("\n".join(body), encoding="utf-8")
    return path


def execution_context_files(config: dict[str, Any], task_id: str | None) -> list[str]:
    files = ["AI_COLLABORATION_GUIDE.md"]
    try:
        brief = write_task_brief(config, task_id)
    except Exception as exc:
        write_activity_log(
            config,
            {
                "type": "task_brief_generation_failed",
                "task_id": task_id,
                "message": f"Fell back to minimal execution context after task brief generation failed: {type(exc).__name__}: {exc}",
            },
        )
        files.append("ai-status.json")
        return unique_strings(files)
    if brief is not None:
        files.append(relpath(brief))
    if WORKER_ANCHOR_SPEC_PATH.exists():
        files.append(relpath(WORKER_ANCHOR_SPEC_PATH))
    if CLOSEOUT_SPEC_PATH.exists():
        files.append(relpath(CLOSEOUT_SPEC_PATH))
    files.append("ai-status.json")
    return unique_strings(files)


def write_failure_evidence(
    config: dict[str, Any],
    *,
    worker: dict[str, Any],
    reason: str | None,
    failure_kind: str | None = None,
) -> str | None:
    run_id = str(worker.get("run_id") or "").strip()
    if not run_id:
        return None
    path = evidence_dir(config) / f"{normalize_agent_id(run_id) or run_id}.json"
    ensure_parent(path)
    payload = {
        "recorded_at": utc_now(),
        "task_id": worker.get("task_id"),
        "run_id": run_id,
        "provider": worker.get("provider"),
        "agent_id": worker.get("agent_id"),
        "failure_kind": failure_kind,
        "reason": reason or "",
        "log_path": worker.get("log_path"),
        "session_id": worker.get("session_id"),
        "queue_event_id": worker.get("queue_event_id"),
    }
    write_json(path, payload)
    return relpath(path)


def write_approval_evidence(
    config: dict[str, Any],
    *,
    approval_id: str | None,
    stage: str,
    payload: dict[str, Any],
) -> str | None:
    approval_slug = normalize_agent_id(approval_id or "approval") or "approval"
    stage_slug = normalize_agent_id(stage) or "event"
    path = evidence_dir(config) / f"{approval_slug}-{stage_slug}.json"
    ensure_parent(path)
    write_json(
        path,
        {
            "recorded_at": utc_now(),
            "approval_id": approval_id,
            "stage": stage,
            **payload,
        },
    )
    return relpath(path)


def to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


if __name__ == "__main__":
    print("This module is shared by the orchestrator scripts and is not meant to be run directly.", file=sys.stderr)
    raise SystemExit(1)
