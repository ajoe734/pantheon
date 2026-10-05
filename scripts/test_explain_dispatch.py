from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".orchestrator"))

import test_supervisor as fixtures  # noqa: E402
from explain_dispatch import supervisor_explain_dispatch_for_task  # noqa: E402


def test_review_only_agent_reports_distinct_reason_on_owner_lane() -> None:
    config = fixtures.config_fixture()
    config["worker_reassignment"]["review_only_agents"] = ["Codex"]
    state = fixtures.with_healthy_delivery_health(
        config, {"workers": {}, "queue": {"events": {}}}
    )
    task = fixtures.task_fixture()
    result = supervisor_explain_dispatch_for_task(
        config, state, task["id"], target_agent_filter="Codex",
        status={"tasks": [task]}, live_total=0, activity_events=[],
    )
    trace = result["agents"]["Codex"]
    assert trace["blocked"] and trace["first_blocking_gate"] == "review_only"
