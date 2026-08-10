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
metadata = json.loads(os.environ["TASK_METADATA_JSON"])
spec = metadata["dev_bridge"]["task_spec"]
task = {
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
}
state["tasks"] = [item for item in state.get("tasks", []) if item.get("id") != task["id"]]
state["tasks"].append(task)
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
    "metadata": metadata,
}
with (root / "calls.jsonl").open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(record, sort_keys=True) + "\\n")
(root / "assigned.txt").write_text(" ".join(sys.argv[1:]), encoding="utf-8")
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
