#!/usr/bin/env python3
"""Explicit, local seed script for developer and CI operator surface data.

Migration context: ACG-RS-RETIRE-E2E-SEED-FIXTURES-V2-20260829.

Previously imported `_default_read_data()` from `read_store.py`, coupling local
file seeding to legacy read-store internals. This module now owns its explicit,
local fixture dictionaries for incidents, postmortems, and evolution decisions.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict


DEFAULT_INCIDENTS: Dict[str, dict[str, Any]] = {
    "inc-20260410-001": {
        "incident_id": "inc-20260410-001",
        "title": "Unexpected drawdown in persona-alpha",
        "severity": "high",
        "status": "open",
        "created_at": "2026-04-10T14:30:00Z",
        "binding_id": "runtime-042",
        "deployment_stage": "live",
        "deployment_plan_id": "plan-F-042",
        "capital_pool_id": "pool-main",
        "persona_capital_binding_id": "binding-042",
        "artifact_id": "artifact-042",
        "artifact_version": "v2.1.0",
        "runtime_id": "runtime-042",
        "trace_id": "trace-inc-20260410-001",
        "telemetry_event_ids": ["tl-001"],
        "evidence_summary": "12% drawdown exceeded 10% threshold; runtime paused pending review.",
        "lineage_ref": "artifact-042@v2.1.0",
    },
    "inc-20260409-002": {
        "incident_id": "inc-20260409-002",
        "title": "Deployment plan plan-F-042 stalled at paper stage",
        "severity": "medium",
        "status": "resolved",
        "created_at": "2026-04-09T08:00:00Z",
        "resolved_at": "2026-04-09T10:30:00Z",
        "binding_id": "runtime-042",
        "deployment_stage": "paper",
        "deployment_plan_id": "plan-F-042",
        "capital_pool_id": "pool-main",
        "persona_capital_binding_id": "binding-042",
        "artifact_id": "artifact-042",
        "artifact_version": "v2.1.0",
        "runtime_id": "runtime-042",
        "trace_id": "trace-inc-20260409-002",
        "telemetry_event_ids": [],
        "evidence_summary": "Promotion gate timeout during artifact validation.",
        "lineage_ref": "artifact-042@v2.1.0",
    },
    "inc-pack-c-001": {
        "id": "inc-pack-c-001",
        "incident_id": "inc-pack-c-001",
        "title": "Pack C paper runtime latency breach",
        "severity": "high",
        "status": "open",
        "created_at": "2026-05-13T03:39:00Z",
        "opened_at": "2026-05-13T03:39:00Z",
        "deployment_stage": "paper",
        "deployment_plan_id": "plan-pack-c-paper-001",
        "capital_pool_id": "pool-pack-a-ops",
        "binding_id": "runtime-pack-c-paper-001",
        "runtime_id": "runtime-pack-c-paper-001",
        "trace_id": "trace-pack-c-incident-001",
        "telemetry_event_ids": ["telemetry-pack-c-latency-001"],
        "evidence_summary": "Paper runtime command latency exceeded the operator-console warning threshold; no live orders were enabled.",
        "lineage_ref": "artifact-pack-b-001@v1.0.0",
    },
}

DEFAULT_POSTMORTEMS: Dict[str, dict[str, Any]] = {
    "pm-20260409-002": {
        "postmortem_id": "pm-20260409-002",
        "incident_id": "inc-20260409-002",
        "title": "Postmortem: Deployment plan F-042 promotion timeout",
        "status": "published",
        "created_at": "2026-04-09T11:00:00Z",
        "published_at": "2026-04-09T12:00:00Z",
        "binding_id": "runtime-042",
        "deployment_stage": "paper",
        "deployment_plan_id": "plan-F-042",
        "capital_pool_id": "pool-main",
        "persona_capital_binding_id": "binding-042",
        "artifact_id": "artifact-042",
        "artifact_version": "v2.1.0",
        "runtime_id": "runtime-042",
        "trace_id": "trace-inc-20260409-002",
        "root_cause": "Promotion gate timeout was set too low (30s) for artifact validation under load.",
        "contributing_factors": [
            "Artifact validation queue became saturated during peak load",
            "Timeout threshold was insufficient for large artifact bundles",
        ],
        "timeline": [
            {"at": "2026-04-09T08:00:00Z", "event": "Incident opened"},
            {"at": "2026-04-09T10:30:00Z", "event": "Incident resolved"},
            {"at": "2026-04-09T11:00:00Z", "event": "Postmortem drafted"},
        ],
        "action_items": [
            "Increase promotion gate timeout to 120s",
            "Add queue-depth alerting for promotion gate",
        ],
        "author_ids": ["platform"],
    },
}

DEFAULT_EVOLUTION_DECISIONS: Dict[str, dict[str, Any]] = {
    "evo-dec-001": {
        "id": "evo-dec-001",
        "action_type": "retrain",
        "risk_level": "medium",
        "status": "approved",
        "incident_ref": "inc-20260410-001",
        "artifact_id": "artifact-042",
        "created_at": "2026-04-10T16:00:00Z",
        "updated_at": "2026-04-11T09:00:00Z",
        "notes": "Approved for retrain after promotion gate timeout root cause confirmed.",
    },
    "evo-dec-88f3a2c1": {
        "id": "evo-dec-88f3a2c1",
        "decision_id": "evo-dec-88f3a2c1",
        "target_type": "candidate_artifact",
        "target_id": "artifact-44d7e9b0",
        "target_version": "v3.1.2",
        "target_stage": "canary",
        "action_type": "freeze_canary",
        "risk_level": "medium",
        "status": "reviewed",
        "decision_state": "reviewed",
        "approval_decision_id": "appr-dec-c5a9f11e",
        "created_at": "2026-04-18T09:32:00Z",
        "updated_at": "2026-04-18T11:05:00Z",
        "created_by_role": "evolution_controller",
        "created_by_id": "evo-controller-01",
        "rationale": "Freeze candidate artifact at canary stage due to sustained slippage drift exceeding the 25% execution drift threshold over three consecutive trading days.",
        "notes": "Slippage drift confirmed. Forwarded to Risk Owner for final approval.",
        "linked_incident_id": None,
        "linked_postmortem_id": None,
        "evidence_refs": [
            {
                "ref_type": "drift_report",
                "ref_id": "drift-rpt-b7c2d3e4",
                "summary": "Execution drift report: 3-day slippage anomaly on artifact-44d7e9b0 canary stage (2026-04-15 – 2026-04-17).",
            },
            {
                "ref_type": "telemetry_summary",
                "ref_id": "telem-sum-9a1f0e22",
                "summary": "Telemetry summary: realized slippage drift at 31% above 20-day baseline.",
            },
        ],
        "threshold_snapshots": [
            {
                "signal_type": "execution_drift",
                "metric_name": "realized_slippage_drift_pct",
                "observed_value": "0.31",
                "threshold_value": "0.25",
                "policy_source": "EVOLUTION_REVIEW_AND_THRESHOLDS.md §7.2",
            },
            {
                "signal_type": "execution_drift",
                "metric_name": "consecutive_anomaly_days",
                "observed_value": "3",
                "threshold_value": "3",
                "policy_source": "EVOLUTION_REVIEW_AND_THRESHOLDS.md §7.2",
            },
        ],
        "review_chain": [
            {
                "step_type": "reviewed",
                "actor_role": "reviewer",
                "actor_id": "reviewer-001",
                "timestamp": "2026-04-18T11:05:00Z",
                "note": "Slippage drift confirmed. Forwarded to Risk Owner for final approval.",
            }
        ],
        "proposed_changes": {
            "summary": "Freeze candidate artifact 'artifact-44d7e9b0' at canary stage due to sustained slippage drift exceeding the 25% execution drift threshold over three consecutive trading days.",
            "target_stage": "canary",
            "downstream_plane": "governance",
            "change_details": [
                {
                    "field": "artifact_stage",
                    "current_value": "canary",
                    "proposed_value": "frozen",
                    "note": "Governance quarantine only; existing canary runtime not automatically stopped unless a companion operational follow-through is initiated.",
                },
                {
                    "field": "admissibility",
                    "current_value": "eligible",
                    "proposed_value": "quarantined",
                    "note": None,
                },
            ],
        },
        "risk_assessment": {
            "risk_summary": "Sustained execution drift on canary stage triggered medium-risk freeze proposal. Slippage drift observed at 31% above the 20-day baseline, exceeding the 25% threshold defined in DriftPolicy.",
            "severity": None,
            "threshold_triggers": [
                {
                    "trigger_type": "execution_drift",
                    "metric": "realized_slippage_drift_pct",
                    "observed_value": "0.31",
                    "threshold_value": "0.25",
                    "threshold_source": "EVOLUTION_REVIEW_AND_THRESHOLDS.md §7.2",
                },
                {
                    "trigger_type": "execution_drift",
                    "metric": "consecutive_anomaly_days",
                    "observed_value": "3",
                    "threshold_value": "3",
                    "threshold_source": "EVOLUTION_REVIEW_AND_THRESHOLDS.md §7.2",
                },
            ],
        },
        "required_approvals": [
            {
                "role": "reviewer",
                "approved_by": "reviewer-001",
                "approved_at": "2026-04-18T11:05:00Z",
                "status": "approved",
            },
            {
                "role": "risk_owner",
                "approved_by": None,
                "approved_at": None,
                "status": "pending",
            },
        ],
        "rollback_followthrough": None,
    },
    "evo-dec-pack-b-001": {
        "id": "evo-dec-pack-b-001",
        "decision_id": "evo-dec-pack-b-001",
        "program_id": "evoprog-pack-b-001",
        "target_type": "candidate_artifact",
        "target_id": "artifact-pack-b-001",
        "target_version": "v1.0.0",
        "target_stage": "paper",
        "action_type": "promote_paper",
        "risk_level": "low",
        "status": "pending",
        "decision_state": "pending",
        "created_at": "2026-05-13T01:00:00Z",
        "updated_at": "2026-05-13T01:00:00Z",
        "created_by_role": "evolution_controller",
        "created_by_id": "evo-controller-fixture",
        "rationale": "Pack B fixture: promote candidate artifact to paper stage for smoke coverage.",
        "notes": "Fixture decision only; no live side effect.",
        "linked_incident_id": None,
        "linked_postmortem_id": None,
        "evidence_refs": [
            {
                "ref_type": "experiment_result",
                "ref_id": "exp-pack-b-001",
                "summary": "Pack B experiment completed with Sharpe > 1.2; eligible for paper promotion.",
            }
        ],
        "threshold_snapshots": [
            {
                "signal_type": "performance",
                "metric_name": "sharpe_ratio",
                "observed_value": "1.25",
                "threshold_value": "1.0",
                "policy_source": "EVOLUTION_REVIEW_AND_THRESHOLDS.md §4.1",
            }
        ],
        "review_chain": [],
        "proposed_changes": {
            "summary": "Promote artifact-pack-b-001 from sealed to paper-stage runtime.",
            "target_stage": "paper",
            "downstream_plane": "governance",
            "change_details": [
                {
                    "field": "artifact_stage",
                    "current_value": "sealed",
                    "proposed_value": "paper",
                    "note": "Fixture paper promotion only; fail-closed, no live or canary authority.",
                }
            ],
        },
        "risk_assessment": {
            "risk_summary": "Low-risk paper promotion within fixture universe; no capital at risk.",
            "severity": "low",
            "threshold_triggers": [],
        },
    },
}


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return default
    return json.loads(text)


def _save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


def _merge_list_records(existing: list[dict[str, Any]], defaults: Dict[str, dict[str, Any]], key: str) -> int:
    existing_by_id = {
        str(item.get(key)): item
        for item in existing
        if isinstance(item, dict) and item.get(key) not in (None, "")
    }
    added = 0
    for record in defaults.values():
        record_id = str(record.get(key) or "")
        if not record_id or record_id in existing_by_id:
            continue
        existing.append(record)
        existing_by_id[record_id] = record
        added += 1
    return added


def seed_incident_store() -> dict[str, int]:
    defaults = {
        "incidents": DEFAULT_INCIDENTS,
        "postmortems": DEFAULT_POSTMORTEMS,
    }
    incidents_dir = Path(os.getenv("INCIDENTS_DATA_DIR", "/tmp/pantheon/incidents"))
    incidents_path = incidents_dir / "incidents.json"
    payload = _load_json(incidents_path, {"incidents": [], "postmortems": []})
    if not isinstance(payload, dict):
        payload = {"incidents": [], "postmortems": []}

    incidents = payload.get("incidents")
    if not isinstance(incidents, list):
        incidents = []
        payload["incidents"] = incidents

    postmortems = payload.get("postmortems")
    if not isinstance(postmortems, list):
        postmortems = []
        payload["postmortems"] = postmortems

    added_incidents = _merge_list_records(incidents, defaults.get("incidents", {}), "incident_id")
    added_postmortems = _merge_list_records(postmortems, defaults.get("postmortems", {}), "postmortem_id")
    _save_json(incidents_path, payload)
    return {
        "incident_records_added": added_incidents,
        "postmortem_records_added": added_postmortems,
    }


def seed_evolution_store() -> dict[str, int]:
    defaults = {
        "evolution_decisions": DEFAULT_EVOLUTION_DECISIONS,
    }
    evolution_dir = Path(os.getenv("EVOLUTION_DATA_DIR", "/tmp/pantheon/evolution"))
    decisions_path = evolution_dir / "decisions.json"
    payload = _load_json(decisions_path, {})
    if not isinstance(payload, dict):
        payload = {}

    added = 0
    for decision_id, record in defaults.get("evolution_decisions", {}).items():
        if decision_id in payload:
            continue
        payload[decision_id] = record
        added += 1

    _save_json(decisions_path, payload)
    return {"evolution_records_added": added}


def main() -> int:
    incident_result = seed_incident_store()
    evolution_result = seed_evolution_store()
    summary = {**incident_result, **evolution_result}
    print(json.dumps(summary, indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

