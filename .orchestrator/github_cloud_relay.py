#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

THIS_DIR = Path(__file__).resolve().parent
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))

from common import config_path, load_config, load_json, utc_now, write_activity_log, write_json


def default_relay_state() -> dict[str, Any]:
    return {
        "version": 1,
        "last_push_at": None,
        "last_pull_at": None,
        "last_error": None,
    }


def load_relay_state(config: dict[str, Any]) -> dict[str, Any]:
    state = load_json(config_path(config, "github_relay_state"), default=default_relay_state()) or {}
    merged = default_relay_state()
    merged.update(state)
    return merged


def save_relay_state(config: dict[str, Any], state: dict[str, Any]) -> None:
    write_json(config_path(config, "github_relay_state"), state)


def relay_request(url: str, token: str | None, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=data, headers=headers, method="POST" if payload is not None else "GET")
    with urllib.request.urlopen(request, timeout=15) as response:
        raw = response.read().decode("utf-8")
        return json.loads(raw) if raw else {}


def push_status_digest(config: dict[str, Any], digest: dict[str, Any]) -> dict[str, Any] | None:
    relay_cfg = (((config.get("github_bus") or {}).get("phase3") or {}).get("cloud_relay") or {})
    url = os.environ.get(relay_cfg.get("url_env", "PANTHEON_GITHUB_RELAY_URL"))
    if not url:
        return None
    token = os.environ.get(relay_cfg.get("token_env", "PANTHEON_GITHUB_RELAY_TOKEN"))
    state = load_relay_state(config)
    response = relay_request(url.rstrip("/") + "/status", token, {"ts": utc_now(), "digest": digest})
    state["last_push_at"] = utc_now()
    state["last_error"] = None
    save_relay_state(config, state)
    write_activity_log(config, {"type": "github_relay_push", "message": "Pushed status digest to cloud relay."})
    return response


def pull_commands(config: dict[str, Any]) -> list[dict[str, Any]]:
    relay_cfg = (((config.get("github_bus") or {}).get("phase3") or {}).get("cloud_relay") or {})
    url = os.environ.get(relay_cfg.get("url_env", "PANTHEON_GITHUB_RELAY_URL"))
    if not url:
        return []
    token = os.environ.get(relay_cfg.get("token_env", "PANTHEON_GITHUB_RELAY_TOKEN"))
    state = load_relay_state(config)
    response = relay_request(url.rstrip("/") + "/commands", token)
    commands = response.get("commands", []) if isinstance(response, dict) else []
    state["last_pull_at"] = utc_now()
    state["last_error"] = None
    save_relay_state(config, state)
    if commands:
        write_activity_log(config, {"type": "github_relay_pull", "message": f"Pulled {len(commands)} command(s) from cloud relay."})
    return commands
