#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ORCHESTRATOR_DIR = ROOT / ".orchestrator"
if str(ORCHESTRATOR_DIR) not in sys.path:
    sys.path.insert(0, str(ORCHESTRATOR_DIR))

from supervisor import (
    explain_dispatch_for_task as supervisor_explain_dispatch_for_task,
    load_config,
    load_status,
)


class DispatchStateLoadError(RuntimeError):
    """The diagnostic could not read the canonical runtime snapshot."""


def status_root_dir(config: dict[str, Any]) -> Path:
    configured = os.environ.get("PANTHEON_STATUS_ROOT")
    return Path(configured).resolve() if configured else ROOT


def bind_status_root_paths(config: dict[str, Any]) -> dict[str, Any]:
    root = status_root_dir(config)
    paths = dict(config.get("paths", {}) or {})
    for name in (
        "status_file",
        "state_file",
        "event_queue",
        "approval_queue",
        "activity_log",
    ):
        value = paths.get(name)
        if value and not Path(str(value)).is_absolute():
            paths[name] = str(root / str(value))
    bound = dict(config)
    bound["paths"] = paths
    return bound


def load_orchestrator_state(config: dict[str, Any]) -> dict[str, Any]:
    value = (config.get("paths", {}) or {}).get("state_file")
    if not value:
        raise DispatchStateLoadError("Supervisor state path is not configured")
    path = Path(str(value))
    if not path.is_absolute():
        path = status_root_dir(config) / path
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DispatchStateLoadError(
            f"Unable to read canonical supervisor state at {path}: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise DispatchStateLoadError(f"Canonical supervisor state at {path} is not an object")
    return payload


def explain_dispatch_for_task(
    config: dict[str, Any],
    state: dict[str, Any],
    task_id: str,
    *,
    target_agent_filter: str | None = None,
) -> dict[str, Any]:
    """Load snapshots, then serialize the supervisor's one decision function."""

    config = bind_status_root_paths(config)
    return supervisor_explain_dispatch_for_task(
        config,
        state,
        task_id,
        target_agent_filter=target_agent_filter,
        status=load_status(config),
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Explain the sole supervisor dispatch decision for a task."
    )
    parser.add_argument("task_id")
    parser.add_argument("--agent")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    config = bind_status_root_paths(load_config())
    try:
        state = load_orchestrator_state(config)
    except DispatchStateLoadError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    explanation = explain_dispatch_for_task(
        config,
        state,
        args.task_id,
        target_agent_filter=args.agent,
    )
    if args.json:
        print(json.dumps(explanation, indent=2, ensure_ascii=False))
        return
    print(f"=== Dispatch Explanation: {explanation.get('task_id')} ===")
    if explanation.get("error"):
        print(f"Error: {explanation['error']}")
        raise SystemExit(1)
    if explanation.get("global_block_reason"):
        print(f"Global: {explanation['global_block_reason']}")
    for agent_name, trace in explanation.get("agents", {}).items():
        if trace["blocked"]:
            print(
                f"{agent_name}: BLOCKED [{trace['first_blocking_gate']}] "
                f"{trace['block_reason']}"
            )
        else:
            print(
                f"{agent_name}: READY reason={trace['candidate_reason']} "
                f"priority={trace['candidate_priority']}"
            )


if __name__ == "__main__":
    main()
