from __future__ import annotations

from pathlib import Path


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
