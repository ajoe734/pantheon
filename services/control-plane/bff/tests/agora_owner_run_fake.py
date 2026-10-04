"""Research-owner run fixture for BFF tests: the owner is the only run source."""
from __future__ import annotations

from typing import Any, Dict

import pytest

_OWNER_STATUS = {"succeeded": "completed", "cancelled": "canceled"}


class OwnerRuns:
    def __init__(self) -> None:
        self.runs: Dict[str, Dict[str, Any]] = {}

    def create_run(self, run: Dict[str, Any]) -> None:
        status = str(run.get("execution_status") or "queued")
        self.runs[run["run_id"]] = {
            **{k: v for k, v in run.items() if k not in {"execution_status", "plan_id", "user_id"}},
            "status": _OWNER_STATUS.get(status, status),
            "created_by": run.get("user_id"),
            "input_refs": [{"type": "research_plan", "id": run["plan_id"]}] if run.get("plan_id") else [],
        }

    def record_execution_receipt(self, receipt: Dict[str, Any]) -> None:
        run = self.runs[receipt["run_id"]]
        run["receipt"] = receipt
        run.setdefault("provenance", receipt.get("mode"))


def install_owner_runs(monkeypatch: pytest.MonkeyPatch) -> OwnerRuns:
    from services.control_plane.bff.agora.strategy_workshop.operations import WorkshopCanonicalOperations

    owner = OwnerRuns()
    monkeypatch.setenv("PANTHEON_RESEARCH_ORCHESTRATOR_API_URL", "http://research-owner.test")
    monkeypatch.setattr(WorkshopCanonicalOperations, "get_research_run", lambda self, run_id: owner.runs.get(run_id))
    return owner
