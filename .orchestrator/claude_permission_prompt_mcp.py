#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

THIS_DIR = Path(__file__).resolve().parent
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))

from approval_queue import create_approval, wait_for_decision
from common import load_config, utc_now, write_activity_log
from permission_broker import evaluate_tool_request


SERVER_NAME = "orchestrator_approval_broker"
TOOL_NAME = "approval_prompt"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Minimal MCP server for Claude permission prompts.")
    parser.add_argument("--config", default=".orchestrator/config.json")
    return parser.parse_args()


def read_message() -> dict[str, Any] | None:
    headers: dict[str, str] = {}
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        if line in {b"\r\n", b"\n"}:
            break
        text = line.decode("utf-8").strip()
        if ":" in text:
            key, value = text.split(":", 1)
            headers[key.strip().lower()] = value.strip()
    length = int(headers.get("content-length", "0") or "0")
    if length <= 0:
        return None
    payload = sys.stdin.buffer.read(length)
    if not payload:
        return None
    return json.loads(payload.decode("utf-8"))


def write_message(payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("utf-8")
    sys.stdout.buffer.write(header)
    sys.stdout.buffer.write(body)
    sys.stdout.buffer.flush()


def text_result(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(payload, ensure_ascii=False),
            }
        ]
    }


def approval_context() -> dict[str, Any]:
    return {
        "worker_run_id": os.environ.get("ORCH_RUN_ID"),
        "task_id": os.environ.get("ORCH_TASK_ID"),
        "agent_id": os.environ.get("ORCH_AGENT_ID"),
    }


def approval_provider(config: dict[str, Any]) -> str:
    provider_id = str(os.environ.get("ORCH_PROVIDER") or "claude").strip().lower() or "claude"
    provider = (config.get("providers", {}) or {}).get(provider_id, {}) or {}
    delivery_mode = str(provider.get("delivery_mode") or "").strip()
    if delivery_mode and delivery_mode != "claude_cli":
        return "claude"
    if provider or provider_id.startswith("claude"):
        return provider_id
    return "claude"


def handle_tool_call(config: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    tool_name = args.get("tool_name") or args.get("toolName")
    tool_input = args.get("input") or args.get("tool_input") or args.get("toolInput") or {}
    decision = evaluate_tool_request(str(tool_name or ""), tool_input, config)
    context = approval_context()
    provider_id = approval_provider(config)

    timeout = float(
        config.get("providers", {})
        .get(provider_id, {})
        .get("broker", {})
        .get("approval_wait_seconds", 3600)
    )
    expires_at = (
        datetime.now(timezone.utc) + timedelta(seconds=timeout)
    ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    if decision["decision"] == "allow":
        write_activity_log(
            config,
            {
                "type": "approval_auto_allow",
                "provider": provider_id,
                "task_id": context.get("task_id"),
                "message": f"Auto-approved {tool_name}",
                "worker_run_id": context.get("worker_run_id"),
                "tool_name": tool_name,
            },
        )
        return text_result({"behavior": "allow", "updatedInput": tool_input})

    if decision["decision"] == "deny":
        write_activity_log(
            config,
            {
                "type": "approval_auto_deny",
                "provider": provider_id,
                "task_id": context.get("task_id"),
                "message": f"Auto-denied {tool_name}",
                "worker_run_id": context.get("worker_run_id"),
                "tool_name": tool_name,
            },
        )
        return text_result({"behavior": "deny", "message": decision["reason"]})

    approval = create_approval(
        config,
        {
            "provider": provider_id,
            "task_id": context.get("task_id"),
            "worker_run_id": context.get("worker_run_id"),
            "agent_id": context.get("agent_id"),
            "session_id": os.environ.get("ORCH_SESSION_ID"),
            "tool_use_id": args.get("tool_use_id") or args.get("toolUseId"),
            "tool_name": tool_name,
            "tool_input": tool_input,
            "risk_class": decision["risk_class"],
            "suggested_rule": decision.get("suggested_rule"),
            "expires_at": expires_at,
            "request_payload": args,
            "broker_decision": decision,
        },
    )
    resolved = wait_for_decision(config, approval["approval_id"], timeout_seconds=timeout)
    if resolved.get("decision") == "allow":
        return text_result({"behavior": "allow", "updatedInput": tool_input})
    message = resolved.get("note") or "Approval was denied or timed out."
    return text_result({"behavior": "deny", "message": message})


def main() -> int:
    args = parse_args()
    config = load_config(args.config)
    while True:
        message = read_message()
        if message is None:
            return 0
        method = message.get("method")
        if method == "initialize":
            write_message(
                {
                    "jsonrpc": "2.0",
                    "id": message.get("id"),
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": SERVER_NAME, "version": "0.1.0"},
                    },
                }
            )
            continue
        if method == "notifications/initialized":
            continue
        if method == "ping":
            write_message({"jsonrpc": "2.0", "id": message.get("id"), "result": {}})
            continue
        if method == "tools/list":
            write_message(
                {
                    "jsonrpc": "2.0",
                    "id": message.get("id"),
                    "result": {
                        "tools": [
                            {
                                "name": TOOL_NAME,
                                "description": "Decide whether Claude may proceed with a permission-requiring tool call.",
                                "inputSchema": {
                                    "type": "object",
                                    "properties": {
                                        "tool_name": {"type": "string"},
                                        "input": {"type": "object"},
                                    },
                                    "required": ["tool_name", "input"],
                                },
                            }
                        ]
                    },
                }
            )
            continue
        if method == "tools/call":
            params = message.get("params", {})
            if params.get("name") != TOOL_NAME:
                write_message(
                    {
                        "jsonrpc": "2.0",
                        "id": message.get("id"),
                        "error": {"code": -32601, "message": f"Unknown tool {params.get('name')}"},
                    }
                )
                continue
            result = handle_tool_call(config, params.get("arguments", {}))
            write_message({"jsonrpc": "2.0", "id": message.get("id"), "result": result})
            continue
        if method == "shutdown":
            write_message({"jsonrpc": "2.0", "id": message.get("id"), "result": {}})
            continue
        if method == "exit":
            return 0
        write_message(
            {
                "jsonrpc": "2.0",
                "id": message.get("id"),
                "error": {"code": -32601, "message": f"Unsupported method: {method}"},
            }
        )


if __name__ == "__main__":
    raise SystemExit(main())
