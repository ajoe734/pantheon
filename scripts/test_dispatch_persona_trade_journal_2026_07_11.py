import json
import os
from pathlib import Path
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/dispatch_persona_trade_journal_2026_07_11.py"


def test_dispatch_is_idempotent_and_disables_live_orders():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "ai-status.json").write_text('{"tasks": []}\n', encoding="utf-8")
        (root / "ai-activity-log.jsonl").write_text("", encoding="utf-8")
        env = {
            **os.environ,
            "PANTHEON_STATUS_ROOT": tmp,
            "PANTHEON_ALLOW_ISOLATED_TEST_WRITES": "1",
        }
        subprocess.run(["python3", str(SCRIPT)], check=True, env=env, capture_output=True, text=True)
        subprocess.run(["python3", str(SCRIPT)], check=True, env=env, capture_output=True, text=True)
        state = json.loads((root / "ai-status.json").read_text(encoding="utf-8"))
        tasks = [task for task in state["tasks"] if task["id"].startswith("PTJ-")]
        assert len(tasks) == 7
        assert all(task["live_order_side_effects_allowed"] is False for task in tasks)
        assert next(task for task in tasks if task["id"] == "PTJ-007")["depends_on"] == [
            "PTJ-002", "PTJ-003", "PTJ-004", "PTJ-005", "PTJ-006"
        ]
