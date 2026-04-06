#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ORCHESTRATOR_DIR = ROOT / ".orchestrator"
DEFAULT_CONFIG_PATH = ORCHESTRATOR_DIR / "config.json"
LOCAL_CONFIG_PATH = ORCHESTRATOR_DIR / "config.local.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def load_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return deepcopy(default)
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return deepcopy(default)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        sanitized = re.sub(r"//.*?$", "", text, flags=re.MULTILINE)
        sanitized = re.sub(r"/\*.*?\*/", "", sanitized, flags=re.DOTALL)
        sanitized = re.sub(r",(\s*[}\]])", r"\1", sanitized)
        return json.loads(sanitized)


def write_json(path: Path, payload: Any) -> None:
    ensure_parent(path)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


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


def run_command(
    command: list[str],
    *,
    cwd: Path | None = None,
    timeout: float | None = None,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=str(cwd or ROOT),
        check=check,
        timeout=timeout,
        text=True,
        capture_output=True,
    )


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


def write_activity_log(config: dict[str, Any], entry: dict[str, Any]) -> None:
    payload = {
        "ts": utc_now(),
        "agent": "Orchestrator",
        **entry,
    }
    append_jsonl(config_path(config, "activity_log"), payload)


def runtime_log_path(prefix: str, target: str) -> Path:
    slug = normalize_agent_id(target) or "unknown"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    suffix = uuid.uuid4().hex[:6]
    return ORCHESTRATOR_DIR / "logs" / f"{stamp}-{prefix}-{slug}-{suffix}.log"


def new_runtime_id(prefix: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{prefix}-{stamp}-{uuid.uuid4().hex[:8]}"


def spawn_background_process(
    command: list[str],
    *,
    cwd: Path | None = None,
    log_path: Path,
    env: dict[str, str] | None = None,
) -> tuple[subprocess.Popen[str], Path]:
    ensure_parent(log_path)
    handle = log_path.open("w", encoding="utf-8")
    process = subprocess.Popen(
        command,
        cwd=str(cwd or ROOT),
        stdout=handle,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
        start_new_session=True,
    )
    return process, log_path


def snapshot_task(task: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": task.get(schema["task_id_field"]),
        "status": task.get(schema["status_field"]),
        "owner": task.get(schema["assignee_field"]),
        "reviewer": task.get(schema["reviewer_field"]),
        "artifacts": list(task.get(schema.get("artifacts_field", "artifacts"), []) or []),
        "next": task.get(schema.get("next_field", "next")),
        "last_update": task.get(schema.get("last_update_field", "last_update")),
    }


def load_status(config: dict[str, Any]) -> dict[str, Any]:
    return load_json(config_path(config, "status_file"), default={}) or {}


def selected_shared_files(config: dict[str, Any]) -> list[Path]:
    files: list[Path] = []
    for key in ("status_file", "current_work", "activity_log", "dashboard"):
        path = config.get("paths", {}).get(key)
        if path:
            files.append(config_path(config, key))
    return files


def serialize_shared_files(paths: list[Path]) -> str:
    return "\n".join(f"- {relpath(path)}" for path in paths)


def to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


if __name__ == "__main__":
    print("This module is shared by the orchestrator scripts and is not meant to be run directly.", file=sys.stderr)
    raise SystemExit(1)
