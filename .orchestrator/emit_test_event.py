#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

THIS_DIR = Path(__file__).resolve().parent
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))

from common import agent_config_for, execution_context_files, load_config, new_runtime_id, utc_now
from runtime_state import enqueue_event


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Send a minimal wake-up test event to a local agent adapter.")
    parser.add_argument("--config", default=".orchestrator/config.json")
    parser.add_argument("--agent", default="codex")
    parser.add_argument("--task-id", default="TEST-001")
    parser.add_argument("--reason", default="manual_test")
    parser.add_argument("--title", default="Manual orchestrator wake-up test")
    parser.add_argument("--dispatch-now", action="store_true", help="Run supervisor once after enqueueing the event.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_config(args.config)
    agent = agent_config_for(config, args.agent)
    message = (
        "你被喚醒了。\n\n"
        "請先閱讀 task brief 與 ai-status.json，找出目前分配給你或等待你回應的 task，然後直接繼續工作。\n\n"
        f"Task ID: {args.task_id}\n"
        f"原因: {args.reason}\n"
        f"標題: {args.title}\n"
    )
    event = {
        "event_id": new_runtime_id("evt"),
        "created_at": utc_now(),
        "event_key": f"manual:{args.task_id}:{agent['id']}:{args.reason}",
        "task_id": args.task_id,
        "target_agent": agent["id"],
        "target_display_name": agent.get("display_name", args.agent),
        "provider": agent.get("provider", agent["id"]),
        "reason": args.reason,
        "message": message,
        "context_files": execution_context_files(config, args.task_id),
        "target_files": [],
        "metadata": {"title": args.title, "manual_test": True},
    }
    enqueue_event(config, event)
    if args.dispatch_now:
        from supervisor import run_once

        run_once(config, watch=False, replay=False)
    print(event)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
