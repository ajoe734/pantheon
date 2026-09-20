"""Keep a supervised AGY turn alive while its commands are unfinished."""
from __future__ import annotations

import json
import os
import sys


def stop_decision(event: dict, *, task_id: str) -> dict:
    # Stop hooks must not override cancellation, provider errors or execution
    # limits. AGY owns task tracking and the existing print timeout still ends
    # the run; this hook has no task-store or process-management authority.
    if (
        task_id
        and event.get("fullyIdle") is False
        and not event.get("error")
        and event.get("terminationReason") in {"NO_TOOL_CALL", "model_stop"}
    ):
        return {
            "decision": "continue",
            "reason": (
                "Required commands are still running. Collect their terminal "
                "output and exit status using the existing command tools before "
                "finishing or handing off. Continue the existing commands; do "
                "not restart or kill them merely to end this turn."
            ),
        }
    return {}


if __name__ == "__main__":
    print(json.dumps(stop_decision(json.load(sys.stdin), task_id=os.environ.get("ORCH_TASK_ID", ""))))
