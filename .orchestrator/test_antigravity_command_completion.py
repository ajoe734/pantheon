from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import unittest


class AntigravityCommandCompletionTests(unittest.TestCase):
    def test_native_hook_continues_only_an_unfinished_worker_turn(self) -> None:
        root = Path(__file__).parent / "antigravity"
        spec = json.loads((root / ".agents/hooks.json").read_text())
        hook = spec["pantheon-worker-command-completion"]["Stop"][0]
        running = {"fullyIdle": False, "terminationReason": "NO_TOOL_CALL", "error": ""}
        cases = [
            (running, "T-PROBE", True),
            ({**running, "terminationReason": "model_stop"}, "T-PROBE", True),
            ({**running, "fullyIdle": True}, "T-PROBE", False),
            ({**running, "error": "quota exhausted"}, "T-PROBE", False),
            ({**running, "terminationReason": "max_steps_exceeded"}, "T-PROBE", False),
            ({**running, "terminationReason": "canceled"}, "T-PROBE", False),
            (running, "", False),
            ({"terminationReason": "model_stop"}, "T-PROBE", False),
        ]
        for event, task_id, continued in cases:
            with self.subTest(event=event, task_id=task_id):
                result = subprocess.run(
                    ["sh", "-c", hook["command"]], cwd=root / ".agents",
                    env={**os.environ, "ORCH_TASK_ID": task_id},
                    input=json.dumps(event), text=True, capture_output=True,
                    timeout=hook["timeout"], check=True,
                )
                decision = json.loads(result.stdout)
                self.assertEqual(decision.get("decision") == "continue", continued)
                if not continued:
                    self.assertEqual(decision, {})


if __name__ == "__main__":
    unittest.main()
