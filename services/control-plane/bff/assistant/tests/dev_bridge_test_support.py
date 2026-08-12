from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any


MATERIALIZING_AI_STATUS_SCRIPT = """import json
import os
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
status_path = root / "ai-status.json"
state = json.loads(status_path.read_text(encoding="utf-8"))
command = sys.argv[1]
payload = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
if command == "dev-bridge-materialize-batch":
    tasks = []
    for row in payload["tasks"]:
        metadata = row["task_metadata"]
        spec = metadata["dev_bridge"]["task_spec"]
        tasks.append({
            "id": spec["id"],
            "title": spec["title"],
            "owner": spec["owner"],
            "reviewer": spec["reviewer"],
            "phase": spec["phase"],
            "depends_on": spec["depends_on"],
            "artifacts": spec["artifacts"],
            "acceptance": spec["acceptance"],
            "summary_zh": spec["summary"],
            "dev_bridge": metadata["dev_bridge"],
        })
    task_ids = {task["id"] for task in tasks}
    existing = {
        item.get("id"): item
        for item in state.get("tasks", [])
        if item.get("id") in task_ids
    }
    if existing and len(existing) != len(tasks):
        print("partial pre-existing packet", file=sys.stderr)
        raise SystemExit(2)
    for task in tasks:
        prior = existing.get(task["id"])
        if prior is not None and prior.get("dev_bridge") != task["dev_bridge"]:
            print(f"bridge provenance conflict: {task['id']}", file=sys.stderr)
            raise SystemExit(2)
    new_tasks = [task for task in tasks if task["id"] not in existing]
    state["tasks"].extend(new_tasks)
    status_path.write_text(json.dumps(state), encoding="utf-8")
    record = {
        "argv": sys.argv[1:],
        "ai_name": os.environ.get("AI_NAME"),
        "auto_worker_markers": {
        key: os.environ[key]
        for key in (
            "ORCH_RUN_ID",
            "ORCH_TASK_ID",
            "PANTHEON_WORKTREE_ROOT",
            "ORCH_WORKSPACE_PATH",
            "ORCH_RUNNER_STATUS_PATH",
            "ORCH_HEARTBEAT_PATH",
        )
        if os.environ.get(key)
        },
        "signing_authority_markers": {
        key: os.environ[key]
        for key in (
            "BRIDGE_SIGNING_PRIVATE_KEY",
            "BRIDGE_SIGNING_KEY",
            "BRIDGE_SIGNING_KEY_ID",
            "PANTHEON_CANONICAL_MUTATION_ASSERTION_KEY",
            "PANTHEON_CANONICAL_MUTATION_ASSERTION_PRIVATE_KEY",
            "PANTHEON_CANONICAL_MUTATION_ASSERTION_KEY_ID",
            "PANTHEON_CANONICAL_MUTATION_ASSERTION_JSON",
        )
        if os.environ.get(key)
        },
        "packet_id": payload["packet_id"],
        "packet_digest": payload["packet_digest"],
        "tasks": payload["tasks"],
    }
    with (root / "calls.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\\n")
    (root / "assigned.txt").write_text(" ".join(sys.argv[1:]), encoding="utf-8")
    raise SystemExit(0)
if command == "dev-bridge-materialize-readback":
    tasks = []
    for row in payload["tasks"]:
        task = next((item for item in state.get("tasks", []) if item.get("id") == row["task_id"]), None)
        if task is None or task.get("dev_bridge") != row["task_metadata"]["dev_bridge"]:
            print(f"Dev bridge materialize readback task is missing: {row['task_id']}", file=sys.stderr)
            raise SystemExit(1)
        tasks.append({
            "taskId": row["task_id"],
            "source": "active",
            "taskSpecHash": row["task_metadata"]["dev_bridge"]["task_spec_hash"],
        })
    print(json.dumps({
        "status": "verified",
        "packetId": payload["packet_id"],
        "packetDigest": payload["packet_digest"],
        "taskIds": [row["task_id"] for row in payload["tasks"]],
        "tasks": tasks,
        "checkpoint": {"sequence": 1},
        "pendingAuditProjections": [],
    }))
    raise SystemExit(0)
raise SystemExit(f"unsupported command: {command}")
"""


def write_materializing_ai_status(repo_root: Path) -> None:
    (repo_root / "ai-status.json").write_text('{"tasks": []}\n', encoding="utf-8")
    scripts_dir = repo_root / "scripts"
    scripts_dir.mkdir(exist_ok=True)
    (scripts_dir / "ai_status.py").write_text(
        MATERIALIZING_AI_STATUS_SCRIPT,
        encoding="utf-8",
    )


def bind_isolated_ai_status_module(ai_status_module: Any, status_root: Path) -> Path:
    """Bind imported governed-status helpers to a test-only root and audit."""

    status_root.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", "-q", str(status_root)],
        check=True,
        capture_output=True,
        text=True,
    )
    ai_status_module.configure_status_root_paths(status_root)
    state = ai_status_module.default_state()
    state["tasks"] = []
    state["handoffs"] = []
    state["blockers"] = []
    state["wave_state"] = {"status": "open"}
    (status_root / "ai-status.json").write_text(
        json.dumps(state, indent=2) + "\n",
        encoding="utf-8",
    )
    (status_root / "ai-activity-log.jsonl").write_text("", encoding="utf-8")
    return status_root
