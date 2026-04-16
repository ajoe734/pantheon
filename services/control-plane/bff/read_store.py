from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional


def _first_existing(paths: List[Path]) -> Optional[Path]:
    for path in paths:
        if path.exists():
            return path
    return None


def _record_key(record: Dict[str, Any], candidates: List[str]) -> Optional[str]:
    for key in candidates:
        value = record.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def _normalize_records(payload: Any, key_candidates: List[str]) -> Dict[str, Dict[str, Any]]:
    if isinstance(payload, dict):
        return {
            str(key): value
            for key, value in payload.items()
            if isinstance(value, dict)
        }
    if isinstance(payload, list):
        normalized: Dict[str, Dict[str, Any]] = {}
        for item in payload:
            if not isinstance(item, dict):
                continue
            key = _record_key(item, key_candidates)
            if key:
                normalized[key] = item
        return normalized
    return {}


class CanonicalSnapshotAdapter:
    """Best-effort adapter over canonical governance/runtime JSON snapshots.

    The BFF remains read-oriented. When canonical snapshot files are available,
    the read surfaces prefer them. When they are absent, the BFF falls back to
    the local read-model seed so the operator UI still has a safe bootstrap path.
    """

    _DATASETS = {
        "deployment_plans": {
            "env": "PANTHEON_BFF_DEPLOYMENT_PLAN_STORE",
            "dirs": ("PANTHEON_GOVERNANCE_DATA_DIR",),
            "filenames": ("deployment_plans.json",),
            "keys": ["plan_id", "id"],
        },
        "approval_decisions": {
            "env": "PANTHEON_BFF_APPROVAL_DECISION_STORE",
            "dirs": ("PANTHEON_GOVERNANCE_DATA_DIR",),
            "filenames": ("approval_decisions.json",),
            "keys": ["decision_id", "id"],
        },
        "capital_pools": {
            "env": "PANTHEON_BFF_CAPITAL_POOL_STORE",
            "dirs": ("PANTHEON_GOVERNANCE_DATA_DIR",),
            "filenames": ("capital_pools.json",),
            "keys": ["pool_id", "id"],
        },
        "persona_bindings": {
            "env": "PANTHEON_BFF_PERSONA_BINDING_STORE",
            "dirs": ("PANTHEON_GOVERNANCE_DATA_DIR",),
            "filenames": ("persona_capital_bindings.json", "bindings.json"),
            "keys": ["binding_id", "id"],
        },
        "runtime_bindings": {
            "env": "PANTHEON_BFF_RUNTIME_BINDING_STORE",
            "dirs": ("PANTHEON_RUNTIME_DATA_DIR",),
            "filenames": ("runtime_bindings.json",),
            "keys": ["binding_id", "id"],
        },
    }

    def __init__(self) -> None:
        self._cache: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self._cache_meta: Dict[str, tuple[str, int]] = {}

    def _resolve_path(self, dataset: str) -> Optional[Path]:
        spec = self._DATASETS[dataset]
        explicit = os.getenv(spec["env"], "").strip()
        if explicit:
            return Path(explicit)
        candidates: List[Path] = []
        for dir_env in spec["dirs"]:
            base = os.getenv(dir_env, "").strip()
            if not base:
                continue
            for filename in spec["filenames"]:
                candidates.append(Path(base) / filename)
        return _first_existing(candidates)

    def _load_dataset(self, dataset: str) -> tuple[bool, Dict[str, Dict[str, Any]]]:
        path = self._resolve_path(dataset)
        if path is None or not path.exists():
            return False, {}

        stat = path.stat().st_mtime_ns
        cache_key = str(path)
        if self._cache_meta.get(dataset) == (cache_key, stat):
            return True, self._cache.get(dataset, {})

        text = path.read_text(encoding="utf-8").strip()
        payload = json.loads(text) if text else {}
        normalized = _normalize_records(payload, self._DATASETS[dataset]["keys"])
        self._cache[dataset] = normalized
        self._cache_meta[dataset] = (cache_key, stat)
        return True, normalized

    def list_records(self, dataset: str) -> tuple[bool, List[Dict[str, Any]]]:
        available, records = self._load_dataset(dataset)
        return available, list(records.values())

    def deployment_plan(self, plan_id: str) -> tuple[bool, Optional[Dict[str, Any]]]:
        available, records = self._load_dataset("deployment_plans")
        return available, records.get(plan_id)

    def approval_decision(self, decision_id: Optional[str]) -> tuple[bool, Optional[Dict[str, Any]]]:
        if not decision_id:
            available, _ = self._load_dataset("approval_decisions")
            return available, None
        available, records = self._load_dataset("approval_decisions")
        return available, records.get(str(decision_id))

    def capital_pool(self, pool_id: Optional[str]) -> tuple[bool, Optional[Dict[str, Any]]]:
        if not pool_id:
            available, _ = self._load_dataset("capital_pools")
            return available, None
        available, records = self._load_dataset("capital_pools")
        return available, records.get(str(pool_id))

    def binding(self, binding_id: Optional[str]) -> tuple[bool, Optional[Dict[str, Any]]]:
        if not binding_id:
            available, _ = self._load_dataset("persona_bindings")
            return available, None
        available, records = self._load_dataset("persona_bindings")
        return available, records.get(str(binding_id))

    def bindings_for_pool(self, pool_id: Optional[str]) -> tuple[bool, List[Dict[str, Any]]]:
        available, records = self._load_dataset("persona_bindings")
        if not available or not pool_id:
            return available, []
        return True, [
            record
            for record in records.values()
            if str(record.get("capital_pool_id") or "") == str(pool_id)
        ]

    def runtime_binding(self, binding_id: Optional[str]) -> tuple[bool, Optional[Dict[str, Any]]]:
        if not binding_id:
            available, _ = self._load_dataset("runtime_bindings")
            return available, None
        available, records = self._load_dataset("runtime_bindings")
        return available, records.get(str(binding_id))

    def runtime_binding_for_plan(self, plan_id: Optional[str]) -> tuple[bool, Optional[Dict[str, Any]]]:
        available, records = self._load_dataset("runtime_bindings")
        if not available or not plan_id:
            return available, None
        for record in records.values():
            if str(record.get("plan_id") or "") == str(plan_id):
                return True, record
        return True, None


def _default_read_data() -> Dict[str, Any]:
    return {
        "deployment_plans": {
            "plan-F-042": {
                "id": "plan-F-042",
                "stage": "paper",
                "current_stage": "none",
                "target_stage": "paper",
                "status": "approved",
                "artifact_id": "artifact-042",
                "artifact_version": "v1.4.2",
                "transition_type": "activate",
                "approval_decision_id": "approval-042",
                "capital_pool_id": "pool-main",
                "binding_ids": ["binding-042"],
                "runtime_binding_id": "runtime-042",
            }
        },
        "approval_decisions": {
            "approval-042": {
                "id": "approval-042",
                "outcome": "approved",
                "state": "decided",
                "reviewer": "governance",
                "decided_at": "2026-04-11T07:55:00Z",
                "risk_level": "low",
            }
        },
        "capital_pools": {
            "pool-main": {
                "id": "pool-main",
                "name": "Primary Capital Pool",
                "status": "ready",
                "owner_id": "ops-team",
                "owner_type": "control-plane",
                "single_runtime_enforced": True,
                "risk_policy_ref": "risk-policy-main",
            }
        },
        "bindings": {
            "binding-042": {
                "id": "binding-042",
                "persona_id": "persona-alpha",
                "capital_pool_id": "pool-main",
                "role": "primary",
                "validity": "active",
                "status": "active",
                "allowed_deployment_scope": "paper",
            }
        },
        "personas": {
            "persona-alpha": {
                "id": "persona-alpha",
                "name": "Alpha Persona",
                "lifecycle_state": "active",
                "mandate": "systematic_crypto_trading",
                "strategy_family": "momentum",
                "created_at": "2026-03-01T00:00:00Z",
                "last_active_at": "2026-04-11T10:00:00Z",
            },
            "p-risk-analyst": {  # consultation responder persona
                "id": "p-risk-analyst",
                "name": "Risk Analyst Persona",
                "lifecycle_state": "active",
                "mandate": "risk_review",
                "strategy_family": "risk_management",
                "created_at": "2026-02-15T00:00:00Z",
                "last_active_at": "2026-04-10T10:14:00Z",
            },
        },
        "sessions": {
            "sess-001": {
                "id": "sess-001",
                "session_id": "sess-001",
                "persona_id": "persona-alpha",
                "session_type": "interactive",
                "status": "active",
                "started_at": "2026-04-11T08:00:00Z",
                "capability_snapshot_id": "cap-001",
                "trace_id": "trace-sess-001",
                "request_id": "req-sess-001",
                "runtime_binding_id": "runtime-042",
                "deployment_stage": "paper",
                "capital_pool_id": "pool-main",
                "last_heartbeat_at": "2026-04-11T11:55:00Z",
                "tools_enabled": ["signal_read", "artifact_load", "telemetry_query"],
                "pool_scope": "pool-main",
            },
            "sess-002": {
                "id": "sess-002",
                "session_id": "sess-002",
                "persona_id": "persona-alpha",
                "session_type": "interactive",
                "status": "idle",
                "started_at": "2026-04-10T14:00:00Z",
                "capability_snapshot_id": "cap-001",
                "trace_id": "trace-sess-002",
                "request_id": "req-sess-002",
                "runtime_binding_id": "runtime-042",
                "deployment_stage": "paper",
                "capital_pool_id": "pool-main",
                "last_heartbeat_at": "2026-04-10T18:00:00Z",
                "tools_enabled": ["signal_read", "artifact_load"],
                "pool_scope": "pool-main",
            },
        },
        "capability_snapshots": {
            "cap-001": {
                "snapshot_id": "cap-001",
                "persona_id": "persona-alpha",
                "effective_tools": ["signal_read", "artifact_load", "telemetry_query"],
                "effective_skills": ["risk_review", "incident_triage"],
                "effective_workflows": ["promotion_review", "incident_response"],
                "restrictions": ["no_live_trade_without_approval"],
                "generated_at": "2026-04-11T07:55:00Z",
                "source_refs": ["persona:persona-alpha", "policy:risk-policy-main"],
            },
        },
        "teaching_sessions": {
            "teach-001": {
                "id": "teach-001",
                "session_id": "teach-001",
                "persona_id": "persona-alpha",
                "opened_by": "operator-oncall",
                "mode": "trainer",
                "status": "completed",
                "started_at": "2026-04-09T09:00:00Z",
                "completed_at": "2026-04-09T09:45:00Z",
                "current_control_state": "released",
                "topic": "drawdown_threshold_tuning",
                "operator_id": "operator-oncall",
                "outcomes": [
                    "Adjusted drawdown threshold from 10% to 8% for pool-main",
                    "Added queue-depth alerting for promotion gate",
                ],
                "session_artifacts": ["artifact-042"],
            },
        },
        "runtime_bindings": {
            "runtime-042": {
                "id": "runtime-042",
                "runtime_id": "runtime-042",
                "deployment_mode": "paper",
                "deployment_stage": "none",
                "status": "idle",
                "plan_id": "plan-F-042",
                "artifact_id": "artifact-042",
                "artifact_version": "v2.1.0",
            }
        },
        "rollbacks": {
            "runtime-042": []
        },
        "allowed_actions": {
            "plan-F-042": {
                "canPromoteToPaper": True
            }
        },
        "latest_runs": {
            "plan-F-042": {
                "progress": 0.82
            }
        },
        "review_summaries": {
            "plan-F-042": {
                "riskSummary": "No unresolved severity-1 or severity-2 incidents."
            }
        },
        # ------------------------------------------------------------------ #
        # Incident surfaces (IN-01 – IN-05)
        # ------------------------------------------------------------------ #
        "incidents": {
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
        },
        "postmortems": {
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
        },
        "kill_switch": {
            "active": False,
            "active_freeze_orders": [],
            "last_checked_at": "2026-04-11T12:00:00Z",
            "safe_mode_status": "off",
        },
        # Cross-references for composed views
        "evolution_decisions": {
            "evo-dec-001": {
                "id": "evo-dec-001",
                "action_type": "retrain",
                "risk_level": "medium",
                "status": "approved",
                "incident_ref": "inc-20260410-001",
                "artifact_id": "artifact-042",
                "created_at": "2026-04-10T16:00:00Z",
            },
        },
        "rollbacks_by_incident": {
            "inc-20260410-001": [
                {
                    "id": "rb-001",
                    "runtime_id": "runtime-042",
                    "action_type": "rollback",
                    "from_version": "v2.1.0",
                    "to_version": "v2.0.0",
                    "status": "completed",
                    "initiated_at": "2026-04-10T14:45:00Z",
                    "completed_at": "2026-04-10T14:50:00Z",
                    "initiated_by": "operator-oncall",
                    "reason": "Excessive drawdown triggered automatic rollback",
                }
            ],
            "inc-20260409-002": [],
        },
        "telemetry_summaries": {
            "runtime-042": {
                "runtime_id": "runtime-042",
                "window": "1h",
                "pnl": -0.12,
                "drawdown": 0.125,
                "sharpe_ratio": -0.8,
                "total_trades": 47,
                "fill_rate": 0.94,
                "avg_slippage_bps": 3.2,
                "collected_at": "2026-04-10T15:00:00Z",
            },
        },
        # EV-03: Freeze orders
        "freeze_orders": {
            "fo-001": {
                "id": "fo-001",
                "scope": "persona",
                "target_id": "persona-alpha",
                "status": "active",
                "created_at": "2026-04-10T14:35:00Z",
                "created_by": "system",
                "reason": "Excessive drawdown triggered automatic freeze",
                "incident_ref": "inc-20260410-001",
            },
        },
        # EV-04: Global rollback list (flat, not grouped by incident)
        "all_rollbacks": [
            {
                "id": "rb-001",
                "runtime_id": "runtime-042",
                "action_type": "rollback",
                "from_version": "v2.1.0",
                "to_version": "v2.0.0",
                "status": "completed",
                "initiated_at": "2026-04-10T14:45:00Z",
                "completed_at": "2026-04-10T14:50:00Z",
                "initiated_by": "operator-oncall",
                "reason": "Excessive drawdown triggered automatic rollback",
                "incident_ref": "inc-20260410-001",
            },
        ],
        # LN-01: Lineage edges
        "lineage_edges": {
            "ln-edge-001": {
                "id": "ln-edge-001",
                "from_artifact_id": "artifact-041",
                "to_artifact_id": "artifact-042",
                "relationship": "derived_from",
                "created_at": "2026-04-09T00:00:00Z",
            },
            "ln-edge-002": {
                "id": "ln-edge-002",
                "from_artifact_id": "artifact-042",
                "to_artifact_id": "artifact-043",
                "relationship": "promoted_to",
                "created_at": "2026-04-10T00:00:00Z",
            },
        },
        # ------------------------------------------------------------------ #
        # Consultation surfaces (CS-01 – CS-06)
        # ------------------------------------------------------------------ #

        # CS-01/CS-02/CS-03/CS-04/CS-05: SessionPersona records with
        # session_type = "consult" or "committee".
        # All fields are the canonical SessionPersona fields from
        # PERSONA_RUNTIME_MODEL.md §14, plus metadata.consultation.*
        # materialized by the Persona Plane.
        "consultation_sessions": {
            "cs-20260410-001": {
                "session_id": "cs-20260410-001",
                "persona_id": "persona-alpha",
                "session_type": "consult",
                "status": "terminated",
                "started_at": "2026-04-10T10:00:00Z",
                "ended_at": "2026-04-10T10:15:00Z",
                "capability_snapshot_id": "cap-001",
                "trace_id": "trace-cs-20260410-001",
                "request_id": "req-cs-20260410-001",
                "context_bundle_ref": "workspace://consultation-context/cs-20260410-001",
                "task_ref": None,
                "runtime_binding_id": None,
                "deployment_stage": None,
                "capital_pool_id": None,
                "metadata": {
                    "consultation": {
                        "consultation_type": "pre_deployment",
                        "requester_session_id": "cs-20260410-001",
                        "responder_session_ids": ["cs-resp-20260410-001"],
                        "committee_session_ids": [],
                        "consult_policy_ref": "cp-risk-analyst",
                        "trigger_rule": "pre_deployment_live",
                        "required_reviewers": 1,
                        "required_committees": [],
                        "forbidden_solo_actions": ["approve_live_deployment"],
                        "actual_reviewers": 1,
                        "outcome": "conditional",
                        "rationale_ref": "workspace://consultation-rationales/cs-20260410-001",
                        "evidence_refs": [
                            {
                                "id": "ev-001",
                                "type": "evidence_link",
                                "evidence_type": "telemetry",
                                "artifact_ref": "artifact-042",
                                "description": "30-day performance metrics",
                                "link": "/api/v1/telemetry/artifact-042/performance?time_range=30d",
                            },
                            {
                                "id": "ev-002",
                                "type": "evidence_link",
                                "evidence_type": "lineage",
                                "artifact_ref": "artifact-042",
                                "description": "Full lineage chain for artifact-042",
                                "link": "/api/v1/lineage?artifact_id=artifact-042",
                            },
                        ],
                        "escalation_path": None,
                    }
                },
            },
            "cs-resp-20260410-001": {
                "session_id": "cs-resp-20260410-001",
                "persona_id": "p-risk-analyst",
                "session_type": "consult",
                "status": "terminated",
                "started_at": "2026-04-10T10:00:30Z",
                "ended_at": "2026-04-10T10:14:00Z",
                "capability_snapshot_id": "cap-001",
                "trace_id": "trace-cs-resp-20260410-001",
                "request_id": "req-cs-resp-20260410-001",
                "context_bundle_ref": "workspace://consultation-context/cs-20260410-001",
                "task_ref": None,
                "runtime_binding_id": None,
                "deployment_stage": None,
                "capital_pool_id": None,
                "metadata": {
                    "consultation": {
                        "consultation_type": "pre_deployment",
                        "consult_policy_ref": "cp-risk-analyst",
                        "root_session_id": "cs-20260410-001",
                    }
                },
            },
        },
        # CS-06: ConsultPolicy records keyed by persona_id
        "consult_policies": {
            "p-risk-analyst": {
                "id": "cp-risk-analyst",
                "persona_id": "p-risk-analyst",
                "required_reviewers": 1,
                "required_committees": [],
                "trigger_rules": [
                    {
                        "condition": "pre_deployment_live",
                        "description": "Risk analyst must review before any live deployment",
                    },
                ],
                "forbidden_solo_actions": ["approve_live_deployment"],
                "escalation_rules": [
                    {
                        "trigger": "responder_rejects",
                        "escalate_to": "governance_committee",
                    }
                ],
            },
            "persona-alpha": {
                "id": "cp-alpha",
                "persona_id": "persona-alpha",
                "required_reviewers": 1,
                "required_committees": [],
                "trigger_rules": [
                    {
                        "condition": "pre_deployment_live",
                        "description": "Must consult before any live deployment",
                    },
                    {
                        "condition": "macro_regime_shift",
                        "description": "Must consult when macro regime shift detected",
                    },
                ],
                "forbidden_solo_actions": [
                    "approve_live_deployment",
                    "increase_capital_allocation_above_20pct",
                ],
                "escalation_rules": [
                    {
                        "trigger": "responder_rejects",
                        "escalate_to": "governance_committee",
                    }
                ],
            },
        },
        # TL-03: Telemetry performance by artifact
        "telemetry_performance": {
            "artifact-042": {
                "artifact_id": "artifact-042",
                "artifact_version": "v2.1.0",
                "window": "24h",
                "data_points": [
                    {"timestamp": "2026-04-10T14:00:00Z", "pnl": -0.05, "drawdown": 0.06},
                    {"timestamp": "2026-04-10T15:00:00Z", "pnl": -0.12, "drawdown": 0.125},
                ],
                "summary": {
                    "total_pnl": -0.12,
                    "max_drawdown": 0.125,
                    "sharpe_ratio": -0.8,
                    "total_trades": 47,
                    "fill_rate": 0.94,
                    "avg_slippage_bps": 3.2,
                },
                "collected_at": "2026-04-10T15:00:00Z",
            },
        },
    }


class ReadSurfaceStore:
    def __init__(self, storage_path: str) -> None:
        self._path = Path(storage_path)
        self._data: Dict[str, Any] = {}
        self._canonical = CanonicalSnapshotAdapter()
        self._load_or_seed()

    def _load_or_seed(self) -> None:
        if self._path.exists():
            raw = self._path.read_text().strip()
            if raw:
                self._data = json.loads(raw)
                return
        self._data = _default_read_data()
        self._save()

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._data, indent=2, ensure_ascii=True))

    @staticmethod
    def _project_canonical_deployment_plan(
        raw: Dict[str, Any],
        runtime_binding_id: Optional[str],
    ) -> Dict[str, Any]:
        plan_id = str(raw.get("plan_id") or raw.get("id") or "")
        binding_id = raw.get("binding_id")
        binding_ids = [str(binding_id)] if binding_id else []
        return {
            "id": plan_id,
            "plan_id": plan_id,
            "stage": raw.get("target_stage") or raw.get("stage") or raw.get("current_stage"),
            "current_stage": raw.get("current_stage"),
            "target_stage": raw.get("target_stage") or raw.get("stage"),
            "artifact_id": raw.get("artifact_id"),
            "artifact_version": raw.get("artifact_version"),
            "approval_decision_id": raw.get("approval_decision_id"),
            "capital_pool_id": raw.get("capital_pool_id"),
            "binding_ids": binding_ids,
            "runtime_binding_id": runtime_binding_id or raw.get("runtime_binding_id"),
            "status": raw.get("status"),
            "transition_type": raw.get("transition_type"),
        }

    @staticmethod
    def _project_canonical_approval_decision(raw: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": raw.get("decision_id") or raw.get("id"),
            "outcome": raw.get("decision") or raw.get("outcome"),
            "reviewer": raw.get("actor_id") or raw.get("reviewer"),
            "actor_role": raw.get("actor_role"),
            "decided_at": raw.get("decided_at"),
            "risk_level": raw.get("risk_level"),
            "state": raw.get("decision_state") or raw.get("state"),
            "rationale": raw.get("rationale"),
        }

    @staticmethod
    def _project_canonical_capital_pool(raw: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": raw.get("pool_id") or raw.get("id"),
            "name": raw.get("name"),
            "status": raw.get("status"),
            "owner_id": raw.get("owner_id"),
            "owner_type": raw.get("owner_type"),
            "single_runtime_enforced": raw.get("single_runtime_enforced", True),
            "risk_policy_ref": raw.get("risk_policy_ref"),
        }

    @staticmethod
    def _project_canonical_binding(raw: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": raw.get("binding_id") or raw.get("id"),
            "persona_id": raw.get("persona_id"),
            "capital_pool_id": raw.get("capital_pool_id"),
            "role": raw.get("role"),
            "status": raw.get("status"),
            "approval_decision_id": raw.get("approval_decision_id"),
            "allowed_deployment_scope": raw.get("allowed_deployment_scope"),
        }

    @staticmethod
    def _project_canonical_runtime_binding(raw: Dict[str, Any]) -> Dict[str, Any]:
        binding_id = raw.get("binding_id") or raw.get("id")
        return {
            "id": binding_id,
            "runtime_id": raw.get("runtime_id") or binding_id,
            "deployment_stage": raw.get("deployment_mode") or raw.get("deployment_stage"),
            "status": raw.get("status"),
            "plan_id": raw.get("plan_id"),
            "capital_pool_id": raw.get("capital_pool_id"),
            "artifact_id": raw.get("artifact_id"),
            "artifact_version": raw.get("artifact_version"),
            "persona_capital_binding_id": raw.get("persona_capital_binding_id"),
        }

    def _derive_can_promote_to_paper(
        self,
        plan: Optional[Dict[str, Any]],
        decision: Optional[Dict[str, Any]],
    ) -> bool:
        if not plan:
            return False
        target_stage = str(plan.get("target_stage") or plan.get("stage") or "").lower()
        current_stage = str(plan.get("current_stage") or "").lower()
        plan_status = str(plan.get("status") or "").lower()
        decision_outcome = str((decision or {}).get("outcome") or "").lower()
        return (
            target_stage == "paper"
            and current_stage != "paper"
            and decision_outcome in {"approved", "approved_with_conditions"}
            and plan_status not in {"rejected", "aborted", "failed", "executed"}
        )

    # ------------------------------------------------------------------ #
    # Catalog list surfaces (PS/CP/DP/RT)
    # ------------------------------------------------------------------ #

    def list_personas(
        self,
        lifecycle_state: Optional[str] = None,
        mandate: Optional[str] = None,
        strategy_family: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        personas = list(self._data.get("personas", {}).values())
        if lifecycle_state:
            personas = [p for p in personas if p.get("lifecycle_state") == lifecycle_state]
        if mandate:
            personas = [p for p in personas if p.get("mandate") == mandate]
        if strategy_family:
            personas = [p for p in personas if p.get("strategy_family") == strategy_family]
        return sorted(personas, key=lambda x: x.get("created_at", ""), reverse=True)

    def list_capital_pools(
        self,
        status: Optional[str] = None,
        risk_policy_ref: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        available, raw_pools = self._canonical.list_records("capital_pools")
        if available:
            pools = [self._project_canonical_capital_pool(pool) for pool in raw_pools]
        else:
            pools = list(self._data.get("capital_pools", {}).values())
        if status:
            pools = [p for p in pools if p.get("status") == status]
        if risk_policy_ref:
            pools = [p for p in pools if p.get("risk_policy_ref") == risk_policy_ref]
        return sorted(pools, key=lambda x: x.get("id", ""))

    def list_bindings(
        self,
        capital_pool_id: Optional[str] = None,
        role: Optional[str] = None,
        validity: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        available, raw_bindings = self._canonical.list_records("persona_bindings")
        if available:
            bindings = [self._project_canonical_binding(binding) for binding in raw_bindings]
        else:
            bindings = list(self._data.get("bindings", {}).values())
        if capital_pool_id:
            bindings = [b for b in bindings if b.get("capital_pool_id") == capital_pool_id]
        if role:
            bindings = [b for b in bindings if b.get("role") == role]
        if validity:
            bindings = [b for b in bindings if b.get("validity") == validity]
        return sorted(bindings, key=lambda x: x.get("id", ""))

    def list_deployment_plans(
        self,
        stage: Optional[str] = None,
        target_pool_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        available, raw_plans = self._canonical.list_records("deployment_plans")
        if available:
            runtime_by_plan: Dict[str, Dict[str, Any]] = {}
            runtime_available, raw_runtime = self._canonical.list_records("runtime_bindings")
            if runtime_available:
                for runtime in raw_runtime:
                    plan_id = str(runtime.get("plan_id") or runtime.get("deployment_plan_id") or "")
                    if plan_id:
                        runtime_by_plan[plan_id] = runtime
            plans = []
            for raw in raw_plans:
                plan_id = str(raw.get("plan_id") or raw.get("id") or "")
                runtime_binding = runtime_by_plan.get(plan_id)
                runtime_binding_id = None
                if runtime_binding:
                    runtime_binding_id = str(runtime_binding.get("binding_id") or runtime_binding.get("id") or "")
                plans.append(self._project_canonical_deployment_plan(raw, runtime_binding_id))
        else:
            plans = list(self._data.get("deployment_plans", {}).values())
        if stage:
            plans = [p for p in plans if str(p.get("stage") or "").lower() == stage.lower()]
        if target_pool_id:
            plans = [
                p for p in plans
                if str(p.get("capital_pool_id") or p.get("target_pool_id") or "") == target_pool_id
            ]
        return sorted(plans, key=lambda x: x.get("id", ""))

    def list_approval_decisions(
        self,
        outcome: Optional[str] = None,
        reviewer: Optional[str] = None,
        time_range: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        available, raw_decisions = self._canonical.list_records("approval_decisions")
        if available:
            decisions = [self._project_canonical_approval_decision(decision) for decision in raw_decisions]
        else:
            decisions = list(self._data.get("approval_decisions", {}).values())
        if outcome:
            decisions = [d for d in decisions if str(d.get("outcome") or "").lower() == outcome.lower()]
        if reviewer:
            decisions = [d for d in decisions if d.get("reviewer") == reviewer]
        # time_range filtering deferred in v1
        return sorted(decisions, key=lambda x: x.get("decided_at", ""), reverse=True)

    def list_runtime_bindings(
        self,
        deployment_mode: Optional[str] = None,
        version: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        available, raw_bindings = self._canonical.list_records("runtime_bindings")
        if available:
            bindings = [self._project_canonical_runtime_binding(binding) for binding in raw_bindings]
        else:
            bindings = list(self._data.get("runtime_bindings", {}).values())
        if deployment_mode:
            bindings = [
                b for b in bindings
                if str(b.get("deployment_stage") or b.get("deployment_mode") or "").lower() == deployment_mode.lower()
            ]
        if version:
            bindings = [
                b for b in bindings
                if str(b.get("artifact_version") or b.get("version") or "") == version
            ]
        return sorted(bindings, key=lambda x: x.get("id", ""))

    def get_deployment_plan(self, plan_id: str) -> Optional[Dict[str, Any]]:
        available, raw = self._canonical.deployment_plan(plan_id)
        if available:
            if raw is None:
                return None
            _, runtime_binding = self._canonical.runtime_binding_for_plan(plan_id)
            runtime_binding_id = None
            if runtime_binding:
                runtime_binding_id = str(runtime_binding.get("binding_id") or runtime_binding.get("id") or "")
            return self._project_canonical_deployment_plan(raw, runtime_binding_id or None)
        return self._data.get("deployment_plans", {}).get(plan_id)

    def get_approval_decision(self, decision_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not decision_id:
            return None
        available, raw = self._canonical.approval_decision(decision_id)
        if available:
            return self._project_canonical_approval_decision(raw) if raw else None
        return self._data.get("approval_decisions", {}).get(decision_id)

    def get_capital_pool(self, pool_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not pool_id:
            return None
        available, raw = self._canonical.capital_pool(pool_id)
        if available:
            return self._project_canonical_capital_pool(raw) if raw else None
        return self._data.get("capital_pools", {}).get(pool_id)

    def get_binding(self, binding_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not binding_id:
            return None
        available, raw = self._canonical.binding(binding_id)
        if available:
            return self._project_canonical_binding(raw) if raw else None
        return self._data.get("bindings", {}).get(binding_id)

    def get_bindings_for_pool(self, pool_id: Optional[str]) -> List[Dict[str, Any]]:
        if not pool_id:
            return []
        available, bindings = self._canonical.bindings_for_pool(pool_id)
        if available:
            return [self._project_canonical_binding(binding) for binding in bindings]
        return [
            binding
            for binding in self._data.get("bindings", {}).values()
            if binding.get("capital_pool_id") == pool_id
        ]

    def get_bindings_for_persona(self, persona_id: Optional[str]) -> Optional[List[Dict[str, Any]]]:
        """Return all bindings where the given persona_id is the owner.

        Returns None when the persona itself cannot be verified (degraded mode).
        """
        if not persona_id:
            return None
        return [
            binding
            for binding in self._data.get("bindings", {}).values()
            if binding.get("persona_id") == persona_id
        ]

    def get_persona(self, persona_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not persona_id:
            return None
        return self._data.get("personas", {}).get(persona_id)

    def get_runtime_binding(self, binding_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not binding_id:
            return None
        available, raw = self._canonical.runtime_binding(binding_id)
        if available:
            return self._project_canonical_runtime_binding(raw) if raw else None
        return self._data.get("runtime_bindings", {}).get(binding_id)

    def get_runtime_binding_by_runtime_id(self, runtime_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not runtime_id:
            return None
        available, raw_bindings = self._canonical.list_records("runtime_bindings")
        if available:
            for raw in raw_bindings:
                raw_runtime_id = str(raw.get("runtime_id") or raw.get("binding_id") or raw.get("id") or "")
                if raw_runtime_id == runtime_id:
                    return self._project_canonical_runtime_binding(raw)
            return None
        for binding in self._data.get("runtime_bindings", {}).values():
            if str(binding.get("runtime_id") or binding.get("id") or "") == runtime_id:
                return binding
        return None

    def get_rollbacks(self, runtime_id: Optional[str]) -> List[Dict[str, Any]]:
        if not runtime_id:
            return []
        return list(self._data.get("rollbacks", {}).get(runtime_id, []))

    def get_allowed_actions(self, plan_id: str) -> Dict[str, Any]:
        plan = self.get_deployment_plan(plan_id)
        decision = self.get_approval_decision(plan.get("approval_decision_id")) if plan else None
        if plan and (plan.get("status") is not None or plan.get("target_stage") is not None):
            return {
                "canPromoteToPaper": self._derive_can_promote_to_paper(plan, decision)
            }
        return self._data.get("allowed_actions", {}).get(plan_id, {"canPromoteToPaper": False})

    def get_latest_run(self, plan_id: str) -> Dict[str, Any]:
        return self._data.get("latest_runs", {}).get(plan_id, {"progress": 0.0})

    def get_review_summary(self, plan_id: str) -> Dict[str, Any]:
        summary = dict(self._data.get("review_summaries", {}).get(plan_id, {}))
        plan = self.get_deployment_plan(plan_id)
        decision = self.get_approval_decision(plan.get("approval_decision_id")) if plan else None
        if decision:
            summary.setdefault("governanceOutcome", decision.get("outcome"))
            summary.setdefault("decisionState", decision.get("state"))
            summary.setdefault("decidedAt", decision.get("decided_at"))
            summary.setdefault("reviewer", decision.get("reviewer"))
            if "riskSummary" not in summary or not summary["riskSummary"]:
                risk_level = decision.get("risk_level")
                if risk_level:
                    summary["riskSummary"] = f"Approval decision risk level: {risk_level}."
        if "riskSummary" not in summary or not summary["riskSummary"]:
            summary["riskSummary"] = "Risk summary unavailable."
        return summary

    # ------------------------------------------------------------------ #
    # Incident surfaces (IN-01 – IN-05)
    # ------------------------------------------------------------------ #

    def list_incidents(
        self,
        status: Optional[str] = None,
        severity: Optional[str] = None,
        affected_pool_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        incidents = list(self._data.get("incidents", {}).values())
        if status:
            incidents = [i for i in incidents if i.get("status") == status]
        if severity:
            incidents = [i for i in incidents if i.get("severity") == severity]
        if affected_pool_id:
            incidents = [i for i in incidents if i.get("capital_pool_id") == affected_pool_id]
        return sorted(incidents, key=lambda x: x.get("created_at", ""), reverse=True)

    def get_incident(self, incident_id: str) -> Optional[Dict[str, Any]]:
        return self._data.get("incidents", {}).get(incident_id)

    def list_postmortems(self, time_range: Optional[str] = None) -> List[Dict[str, Any]]:
        # time_range deferred — v1 returns all postmortems
        return list(self._data.get("postmortems", {}).values())

    def get_postmortem(self, report_id: str) -> Optional[Dict[str, Any]]:
        return self._data.get("postmortems", {}).get(report_id)

    def get_postmortem_by_incident(self, incident_id: str) -> Optional[Dict[str, Any]]:
        for pm in self._data.get("postmortems", {}).values():
            if pm.get("incident_id") == incident_id:
                return pm
        return None

    def get_kill_switch_status(self) -> Dict[str, Any]:
        ks = self._data.get("kill_switch", {})
        return {
            "active": ks.get("active", False),
            "active_freeze_orders": ks.get("active_freeze_orders", []),
            "last_checked_at": ks.get("last_checked_at", ""),
            "safe_mode_status": ks.get("safe_mode_status", "off"),
        }

    # ------------------------------------------------------------------ #
    # Composed view helpers
    # ------------------------------------------------------------------ #

    def get_evolution_decision(self, decision_id: str) -> Optional[Dict[str, Any]]:
        return self._data.get("evolution_decisions", {}).get(decision_id)

    def get_evolution_decisions_by_incident(self, incident_id: str) -> List[Dict[str, Any]]:
        return [
            d for d in self._data.get("evolution_decisions", {}).values()
            if d.get("incident_ref") == incident_id
        ]

    def get_rollbacks_by_incident(self, incident_id: str) -> List[Dict[str, Any]]:
        return list(self._data.get("rollbacks_by_incident", {}).get(incident_id, []))

    def get_telemetry_summary(self, runtime_id: str) -> Optional[Dict[str, Any]]:
        return self._data.get("telemetry_summaries", {}).get(runtime_id)

    # ------------------------------------------------------------------ #
    # Evolution surfaces (EV-01 – EV-04)
    # ------------------------------------------------------------------ #

    def list_evolution_decisions(
        self,
        action_type: Optional[str] = None,
        risk_level: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        decisions = list(self._data.get("evolution_decisions", {}).values())
        if action_type:
            decisions = [d for d in decisions if d.get("action_type") == action_type]
        if risk_level:
            decisions = [d for d in decisions if d.get("risk_level") == risk_level]
        if status:
            decisions = [d for d in decisions if d.get("status") == status]
        return sorted(decisions, key=lambda x: x.get("created_at", ""), reverse=True)

    def get_evolution_decision_by_id(self, decision_id: str) -> Optional[Dict[str, Any]]:
        return self._data.get("evolution_decisions", {}).get(decision_id)

    def list_freeze_orders(
        self,
        status: Optional[str] = None,
        scope: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        orders = list(self._data.get("freeze_orders", {}).values())
        if status:
            orders = [o for o in orders if o.get("status") == status]
        if scope:
            orders = [o for o in orders if o.get("scope") == scope]
        return sorted(orders, key=lambda x: x.get("created_at", ""), reverse=True)

    def list_all_rollbacks(
        self,
        runtime_id: Optional[str] = None,
        action_type: Optional[str] = None,
        time_range: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        rollbacks = list(self._data.get("all_rollbacks", []))
        if runtime_id:
            rollbacks = [r for r in rollbacks if r.get("runtime_id") == runtime_id]
        if action_type:
            rollbacks = [r for r in rollbacks if r.get("action_type") == action_type]
        # time_range filtering deferred in v1
        return sorted(rollbacks, key=lambda x: x.get("initiated_at", ""), reverse=True)

    # ------------------------------------------------------------------ #
    # Lineage surfaces (LN-01 – LN-03)
    # ------------------------------------------------------------------ #

    def list_lineage_edges(
        self,
        artifact_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        edges = list(self._data.get("lineage_edges", {}).values())
        if artifact_id:
            edges = [
                e for e in edges
                if e.get("from_artifact_id") == artifact_id or e.get("to_artifact_id") == artifact_id
            ]
        return sorted(edges, key=lambda x: x.get("created_at", ""), reverse=True)

    def get_lineage_edge(self, edge_id: str) -> Optional[Dict[str, Any]]:
        return self._data.get("lineage_edges", {}).get(edge_id)

    def get_lineage_graph(
        self,
        root_type: Optional[str] = None,
        root_id: Optional[str] = None,
        depth: int = 3,
    ) -> List[Dict[str, Any]]:
        """Return lineage edges reachable from a root artifact within depth hops.

        v1: returns all edges that touch the root_id directly (depth=1 semantics).
        Production implementation would traverse the graph iteratively up to depth.
        """
        edges = list(self._data.get("lineage_edges", {}).values())
        if root_id:
            edges = [
                e for e in edges
                if e.get("from_artifact_id") == root_id or e.get("to_artifact_id") == root_id
            ]
        if root_type:
            # v1: root_type filtering is a no-op since edges don't carry type metadata
            # Production would filter by artifact type via registry lookup
            pass
        return sorted(edges, key=lambda x: x.get("created_at", ""), reverse=True)

    # ------------------------------------------------------------------ #
    # Telemetry surfaces (TL-01 – TL-03)
    # ------------------------------------------------------------------ #

    def list_telemetry_events(
        self,
        pool_id: Optional[str] = None,
        artifact_id: Optional[str] = None,
        time_range: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """TL-01: Return telemetry events with optional filters.

        v1: returns telemetry summaries as event-like records.
        Production would ingest raw telemetry events from the event store.
        """
        # v1: adapt telemetry summaries as event list
        events = []
        for runtime_id, summary in self._data.get("telemetry_summaries", {}).items():
            event = {
                "id": f"tl-evt-{runtime_id}",
                "runtime_id": runtime_id,
                "type": "telemetry_snapshot",
                "timestamp": summary.get("collected_at", ""),
                "metrics": {
                    "pnl": summary.get("pnl"),
                    "drawdown": summary.get("drawdown"),
                    "sharpe_ratio": summary.get("sharpe_ratio"),
                    "total_trades": summary.get("total_trades"),
                    "fill_rate": summary.get("fill_rate"),
                    "avg_slippage_bps": summary.get("avg_slippage_bps"),
                },
            }
            events.append(event)

        if artifact_id:
            # Filter by artifact_id via runtime_id match (v1: artifacts map to runtimes)
            events = [e for e in events if e["runtime_id"] == artifact_id]
        if pool_id:
            # v1: pool_id filtering not available in telemetry summaries
            # Production would join telemetry with pool membership
            pass
        # time_range filtering deferred to v2
        return sorted(events, key=lambda x: x.get("timestamp", ""), reverse=True)

    # ------------------------------------------------------------------ #
    # Telemetry performance (TL-03)
    # ------------------------------------------------------------------ #

    def get_telemetry_performance(self, artifact_id: str) -> Optional[Dict[str, Any]]:
        return self._data.get("telemetry_performance", {}).get(artifact_id)

    # ------------------------------------------------------------------ #
    # Persona session surfaces (PS-03, PS-05)
    # ------------------------------------------------------------------ #

    def get_sessions_for_persona(self, persona_id: Optional[str]) -> Optional[List[Dict[str, Any]]]:
        """PS-03: Return active sessions for a persona.

        Returns None when the persona cannot be verified (degraded mode).
        """
        if not persona_id:
            return None
        return [
            s for s in self._data.get("sessions", []).values()
            if s.get("persona_id") == persona_id
        ]

    def list_sessions_for_persona(
        self,
        persona_id: Optional[str],
        status: Optional[str] = None,
    ) -> Optional[List[Dict[str, Any]]]:
        sessions = self.get_sessions_for_persona(persona_id)
        if sessions is None:
            return None
        if status:
            sessions = [s for s in sessions if s.get("status") == status]
        return sessions

    def get_session(self, session_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not session_id:
            return None
        return self._data.get("sessions", {}).get(session_id)

    def get_teaching_sessions_for_persona(self, persona_id: Optional[str]) -> Optional[List[Dict[str, Any]]]:
        """PS-05: Return teaching sessions for a persona.

        Returns None when the persona cannot be verified (degraded mode).
        """
        if not persona_id:
            return None
        return [
            s for s in self._data.get("teaching_sessions", []).values()
            if s.get("persona_id") == persona_id
        ]

    def list_teaching_sessions_for_persona(
        self,
        persona_id: Optional[str],
        status: Optional[str] = None,
    ) -> Optional[List[Dict[str, Any]]]:
        sessions = self.get_teaching_sessions_for_persona(persona_id)
        if sessions is None:
            return None
        if status:
            sessions = [s for s in sessions if s.get("status") == status]
        return sessions

    def get_capability_snapshot(self, snapshot_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not snapshot_id:
            return None
        return self._data.get("capability_snapshots", {}).get(snapshot_id)

    def get_capability_snapshot_for_persona(self, persona_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not persona_id:
            return None
        for snapshot in self._data.get("capability_snapshots", {}).values():
            if snapshot.get("persona_id") == persona_id:
                return snapshot
        return None

    # ------------------------------------------------------------------ #
    # Consultation surfaces (CS-01 – CS-06)
    # ------------------------------------------------------------------ #

    def list_consultations_for_persona(
        self,
        persona_id: Optional[str],
        consultation_type: Optional[str] = None,
        status: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Optional[List[Dict[str, Any]]]:
        """CS-01: List consultation sessions for a persona.

        Returns None when the persona cannot be verified (degraded mode).
        Only returns sessions where persona_id is the requester (the
        session whose session_id matches metadata.consultation.requester_session_id).
        """
        if not persona_id:
            return None
        sessions = [
            s for s in self._data.get("consultation_sessions", {}).values()
            if s.get("persona_id") == persona_id
            and s.get("session_type") in {"consult", "committee"}
            and s.get("session_id") == (
                (s.get("metadata") or {}).get("consultation", {}).get("requester_session_id")
            )
        ]
        if consultation_type:
            sessions = [
                s for s in sessions
                if (s.get("metadata") or {}).get("consultation", {}).get("consultation_type") == consultation_type
            ]
        if status:
            sessions = [s for s in sessions if s.get("status") == status]
        sessions = sorted(sessions, key=lambda x: x.get("started_at", ""), reverse=True)
        return sessions

    def get_consultation(self, session_id: Optional[str]) -> Optional[Dict[str, Any]]:
        """CS-02: Return a consultation session by session_id."""
        if not session_id:
            return None
        session = self._data.get("consultation_sessions", {}).get(session_id)
        if session is None:
            return None
        if session.get("session_type") not in {"consult", "committee"}:
            return None
        return session

    def _resolve_root_consultation_id(self, session_id: str) -> str:
        """Return the root (requester) session id for a given consultation session_id.

        For requester sessions the id is returned unchanged.
        For responder/committee sessions that carry root_session_id in their
        metadata.consultation, that pointer is followed one level.
        """
        session = self._data.get("consultation_sessions", {}).get(session_id)
        if session is None:
            return session_id
        meta_consult = (session.get("metadata") or {}).get("consultation", {})
        if meta_consult.get("requester_session_id"):
            # Already the root
            return session_id
        root_ref = meta_consult.get("root_session_id")
        if root_ref:
            return root_ref
        return session_id

    def get_consultation_participants(self, session_id: Optional[str]) -> Optional[List[Dict[str, Any]]]:
        """CS-03: Return all participant sessions linked to a consultation.

        The requester session is identified by session_id = metadata.consultation.requester_session_id.
        Responder sessions are identified by metadata.consultation.responder_session_ids.
        Committee sessions are identified by metadata.consultation.committee_session_ids.

        When called with a responder or committee session id, the root session is
        resolved via metadata.consultation.root_session_id so that participants,
        outcome, and evidence are always served from the authoritative root record.
        """
        if not session_id:
            return None
        all_sessions = self._data.get("consultation_sessions", {})
        if session_id not in all_sessions:
            return None
        root_id = self._resolve_root_consultation_id(session_id)
        root = all_sessions.get(root_id)
        if root is None:
            return None
        meta_consult = (root.get("metadata") or {}).get("consultation", {})
        requester_id = meta_consult.get("requester_session_id")
        responder_ids: List[str] = meta_consult.get("responder_session_ids") or []
        committee_ids: List[str] = meta_consult.get("committee_session_ids") or []

        all_sessions = self._data.get("consultation_sessions", {})
        participants = []

        def _role_for(sid: str) -> str:
            if sid == requester_id:
                return "requester"
            if sid in committee_ids:
                return "committee_participant"
            return "responder"

        for sid in [requester_id] + responder_ids + committee_ids:
            if not sid:
                continue
            session = all_sessions.get(sid)
            if session:
                enriched = dict(session)
                enriched["consultation_role"] = _role_for(sid)
                participants.append(enriched)

        return participants

    def get_consultation_outcome(self, session_id: Optional[str]) -> Optional[Dict[str, Any]]:
        """CS-04: Return the consultation outcome projection for a session.

        When called with a responder or committee session id, outcome is resolved
        from the root (requester) session via root_session_id.
        """
        if not session_id:
            return None
        all_sessions = self._data.get("consultation_sessions", {})
        if session_id not in all_sessions:
            return None
        root_id = self._resolve_root_consultation_id(session_id)
        session = self.get_consultation(root_id)
        if session is None:
            return None
        meta_consult = (session.get("metadata") or {}).get("consultation", {})
        return {
            "session_id": session_id,
            "root_session_id": root_id,
            "source_session": f"/api/v1/consultations/{root_id}",
            "metadata": {
                "consultation": {
                    "outcome": meta_consult.get("outcome"),
                    "actual_reviewers": meta_consult.get("actual_reviewers"),
                    "responder_session_ids": meta_consult.get("responder_session_ids", []),
                    "rationale_ref": meta_consult.get("rationale_ref"),
                    "evidence_refs": meta_consult.get("evidence_refs", []),
                    "escalation_path": meta_consult.get("escalation_path"),
                }
            },
        }

    def get_consultation_evidence(self, session_id: Optional[str]) -> Optional[List[Dict[str, Any]]]:
        """CS-05: Return evidence refs attached to a consultation session.

        When called with a responder or committee session id, evidence is resolved
        from the root (requester) session via root_session_id.
        """
        if not session_id:
            return None
        all_sessions = self._data.get("consultation_sessions", {})
        if session_id not in all_sessions:
            return None
        root_id = self._resolve_root_consultation_id(session_id)
        session = self.get_consultation(root_id)
        if session is None:
            return None
        meta_consult = (session.get("metadata") or {}).get("consultation", {})
        return list(meta_consult.get("evidence_refs") or [])

    def get_consult_policy(self, persona_id: Optional[str]) -> Optional[Dict[str, Any]]:
        """CS-06: Return the ConsultPolicy for a persona."""
        if not persona_id:
            return None
        return self._data.get("consult_policies", {}).get(persona_id)

    def get_persona_allowed_actions(self, persona_id: Optional[str]) -> Optional[Dict[str, Any]]:
        """Derive allowed actions for a persona based on lifecycle state and session status.

        Returns None when the persona cannot be verified (degraded mode).
        """
        if not persona_id:
            return None
        persona = self.get_persona(persona_id)
        if not persona:
            return None

        lifecycle_state = persona.get("lifecycle_state", "unknown")
        sessions = self.get_sessions_for_persona(persona_id) or []
        active_sessions = [s for s in sessions if s.get("status") == "active"]

        actions: Dict[str, Any] = {}

        # Persona lifecycle-based actions
        if lifecycle_state == "draft":
            actions["canActivate"] = True
            actions["canEdit"] = True
            actions["canDelete"] = True
        elif lifecycle_state == "active":
            actions["canActivate"] = False
            actions["canEdit"] = True
            actions["canDelete"] = False
            actions["canRetire"] = True
            actions["canPause"] = len(active_sessions) == 0
        elif lifecycle_state == "retired":
            actions["canActivate"] = False
            actions["canEdit"] = False
            actions["canDelete"] = False
            actions["canRetire"] = False
            actions["canPause"] = False

        # Session-based actions
        if active_sessions:
            actions["canTerminateSession"] = True
            actions["canPauseSession"] = True

        # Teaching session inference
        teaching_sessions = self.get_teaching_sessions_for_persona(persona_id) or []
        if teaching_sessions:
            actions["canViewTeachingHistory"] = True

        return actions
