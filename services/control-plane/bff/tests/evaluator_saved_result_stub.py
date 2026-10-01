"""Test double for the persona evaluator's saved result.

Stands in for the evaluator service's persisted provider output: for every ranking
snapshot the BFF admits it "saves" one recommendation per persona. The action
choice below is a fixture-only stand-in for the provider's judgement and exists
only here; production code has no score-to-action rule.
"""
from __future__ import annotations

from typing import Any, Dict, List

ACTION_ORDER = (
    "promote_to_canary_candidate", "increase_research_budget", "grant_tool_access", "reduce_capital_access",
    "require_retraining", "freeze_persona", "suspend_persona", "retire_persona",
)


def stub_actions(item: Dict[str, Any]) -> List[str]:
    c = item.get("components") if isinstance(item.get("components"), dict) else {}
    overall = float(item.get("score") or item.get("overall_score") or 0.0)
    risk, execution, activity = (c.get(k) for k in ("risk_score", "execution_score", "activity_score"))
    out = set()
    if overall >= 85.0 and (risk is None or risk >= 70.0) and (execution is None or execution >= 65.0):
        out |= {"promote_to_canary_candidate", "increase_research_budget", "grant_tool_access"}
    elif overall >= 70.0 and (risk is None or risk >= 60.0):
        out |= {"increase_research_budget", "grant_tool_access"}
    if risk is not None and risk < 55.0:
        out.add("reduce_capital_access")
    if (execution is not None and execution < 55.0) or (activity is not None and activity < 45.0) or overall < 55.0:
        out.add("require_retraining")
    if overall < 55.0:
        out.add("reduce_capital_access")
    for limit, action in ((45.0, "freeze_persona"), (35.0, "suspend_persona"), (25.0, "retire_persona")):
        if overall < limit:
            out.add(action)
    return [a for a in ACTION_ORDER if a in out] or ["require_retraining"]


class SavedEvaluator:
    def __init__(self) -> None:
        self.snapshots: Dict[str, Dict[str, Any]] = {}
        self.latest: Dict[str, str] = {}

    def record(self, record: Dict[str, Any]) -> None:
        self.snapshots[record["ranking_snapshot_id"]] = record
        self.latest[str(record.get("period") or "").upper()] = record["ranking_snapshot_id"]

    def result(self, quarter: str, snapshot_id: str = "") -> Dict[str, Any] | None:
        snapshot = self.snapshots.get(snapshot_id or self.latest.get(quarter.upper(), ""))
        if snapshot is None or str(snapshot.get("period") or "").upper() != quarter.upper():
            return None
        items = []
        for item in snapshot.get("items") or []:
            for action_id in stub_actions(item):
                persona_id = str(item.get("persona_id") or "")
                items.append({
                    "persona_id": persona_id, "action_id": action_id, "rationale": f"provider rationale for {action_id}",
                    "evidence_ref_ids": list(item.get("evidence_ref_ids") or []), "from_state": item.get("state"),
                    "recommendation_id": f"pm12-{quarter.lower()}-{persona_id}-{action_id}",
                    "ranking_snapshot_id": snapshot["ranking_snapshot_id"], "quarter": quarter.upper(),
                    "governance_request": None,
                })
        return {"ranking_snapshot_id": snapshot["ranking_snapshot_id"], "run_id": "stub-run",
                "evaluated_at": "2026-01-01T00:00:00+00:00", "provider": "openclaw", "items": items}

    def recommendation(self, quarter: str, snapshot_id: str, recommendation_id: str) -> Dict[str, Any] | None:
        for rec in (self.result(quarter, snapshot_id) or {}).get("items") or []:
            if rec["recommendation_id"] == recommendation_id:
                return {**rec, "evaluator_run_id": "stub-run", "evaluated_at": "2026-01-01T00:00:00+00:00"}
        return None
