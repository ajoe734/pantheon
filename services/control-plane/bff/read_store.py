from __future__ import annotations

import json
import os
from datetime import datetime, timezone
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


def _parse_rfc3339(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _utc_now_rfc3339() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _default_bff_snapshot_path() -> Path:
    return Path(os.getenv("BFF_DATA_DIR", "/tmp/pantheon/bff")) / "read_surfaces.json"


def _load_snapshot_dataset(
    snapshot_path: Path,
    dataset_key: str,
    key_candidates: List[str],
) -> tuple[bool, Dict[str, Dict[str, Any]], tuple[str, int]]:
    if not snapshot_path.exists():
        return False, {}, ("", 0)

    stat = snapshot_path.stat().st_mtime_ns
    text = snapshot_path.read_text(encoding="utf-8").strip()
    payload = json.loads(text) if text else {}
    if not isinstance(payload, dict):
        return False, {}, ("", 0)

    dataset_payload = payload.get(dataset_key, {})
    normalized = _normalize_records(dataset_payload, key_candidates)
    return True, normalized, (str(snapshot_path), stat)


class CanonicalSnapshotAdapter:
    """Best-effort adapter over canonical governance/runtime JSON snapshots.

    The BFF remains read-oriented. When canonical snapshot files are available,
    the read surfaces prefer them. When they are absent, the normal integration
    path must surface backend unavailability explicitly instead of silently
    inventing local defaults.
    """

    _DATASETS = {
        "deployment_plans": {
            "env": "PANTHEON_BFF_DEPLOYMENT_PLAN_STORE",
            "dirs": ("PANTHEON_GOVERNANCE_DATA_DIR",),
            "filenames": ("deployment_plans.json",),
            "keys": ["plan_id", "id"],
            "snapshot_key": "deployment_plans",
        },
        "approval_decisions": {
            "env": "PANTHEON_BFF_APPROVAL_DECISION_STORE",
            "dirs": ("PANTHEON_GOVERNANCE_DATA_DIR",),
            "filenames": ("approval_decisions.json",),
            "keys": ["decision_id", "id"],
            "snapshot_key": "approval_decisions",
        },
        "capital_pools": {
            "env": "PANTHEON_BFF_CAPITAL_POOL_STORE",
            "dirs": ("PANTHEON_GOVERNANCE_DATA_DIR",),
            "filenames": ("capital_pools.json",),
            "keys": ["pool_id", "id"],
            "snapshot_key": "capital_pools",
        },
        "persona_bindings": {
            "env": "PANTHEON_BFF_PERSONA_BINDING_STORE",
            "dirs": ("PANTHEON_GOVERNANCE_DATA_DIR",),
            "filenames": ("persona_capital_bindings.json", "bindings.json"),
            "keys": ["binding_id", "id"],
            "snapshot_key": "bindings",
        },
        "runtime_bindings": {
            "env": "PANTHEON_BFF_RUNTIME_BINDING_STORE",
            "dirs": ("PANTHEON_RUNTIME_DATA_DIR",),
            "filenames": ("runtime_bindings.json",),
            "keys": ["binding_id", "id"],
            "snapshot_key": "runtime_bindings",
        },
        "registry_entries": {
            "env": "PANTHEON_BFF_REGISTRY_ENTRY_STORE",
            "dirs": ("PANTHEON_REGISTRY_DATA_DIR",),
            "filenames": ("registry_entries.json",),
            "keys": ["artifact_id", "registry_id", "id"],
            "snapshot_key": "registry_entries",
        },
    }

    def __init__(self, *, snapshot_path: Optional[Path] = None) -> None:
        self._cache: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self._cache_meta: Dict[str, tuple[str, int]] = {}
        self._snapshot_path = snapshot_path or _default_bff_snapshot_path()

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
        if path is not None and path.exists():
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

        snapshot_key = self._DATASETS[dataset].get("snapshot_key")
        if snapshot_key:
            available, normalized, cache_meta = _load_snapshot_dataset(
                self._snapshot_path,
                str(snapshot_key),
                self._DATASETS[dataset]["keys"],
            )
            if available:
                if self._cache_meta.get(dataset) != cache_meta:
                    self._cache[dataset] = normalized
                    self._cache_meta[dataset] = cache_meta
                return True, self._cache.get(dataset, normalized)

        return False, {}

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


class ServiceBackedReadAdapter:
    """Best-effort adapter over backend-owned JSON stores produced by services."""

    _DATASETS = {
        "personas": {
            "env": "PANTHEON_BFF_PERSONA_REGISTRY_STORE",
            "dirs": ("PANTHEON_PERSONA_DATA_DIR",),
            "filenames": ("personas.json",),
            "keys": ["persona_id", "id"],
            "snapshot_key": "personas",
        },
        "sessions": {
            "env": "PANTHEON_BFF_PERSONA_SESSION_STORE",
            "dirs": ("PANTHEON_PERSONA_DATA_DIR",),
            "filenames": ("sessions.json",),
            "keys": ["session_id", "id"],
            "snapshot_key": "sessions",
        },
        "capability_snapshots": {
            "env": "PANTHEON_BFF_CAPABILITY_SNAPSHOT_STORE",
            "dirs": (),
            "filenames": (),
            "keys": ["snapshot_id", "id"],
            "snapshot_key": "capability_snapshots",
        },
        "teaching_sessions": {
            "env": "PANTHEON_BFF_TEACHING_SESSION_STORE",
            "dirs": (),
            "filenames": (),
            "keys": ["session_id", "id"],
            "snapshot_key": "teaching_sessions",
        },
        "trainer_previews": {
            "env": "PANTHEON_BFF_TRAINER_PREVIEW_STORE",
            "dirs": (),
            "filenames": (),
            "keys": ["session_id", "id"],
            "snapshot_key": "trainer_previews",
        },
        "consultation_sessions": {
            "env": "PANTHEON_BFF_CONSULTATION_SESSION_STORE",
            "dirs": (),
            "filenames": (),
            "keys": ["session_id", "id"],
            "snapshot_key": "consultation_sessions",
        },
        "consult_policies": {
            "env": "PANTHEON_BFF_CONSULT_POLICY_STORE",
            "dirs": (),
            "filenames": (),
            "keys": ["persona_id", "id"],
            "snapshot_key": "consult_policies",
        },
        "incidents": {
            "env": "PANTHEON_BFF_INCIDENT_STORE",
            "dirs": ("INCIDENTS_DATA_DIR", "POSTMORTEMS_DATA_DIR"),
            "filenames": ("incidents.json",),
            "keys": ["incident_id", "id"],
            "nested_key": "incidents",
            "snapshot_key": "incidents",
        },
        "postmortems": {
            "env": "PANTHEON_BFF_POSTMORTEM_STORE",
            "dirs": ("POSTMORTEMS_DATA_DIR", "INCIDENTS_DATA_DIR"),
            "filenames": ("incidents.json",),
            "keys": ["postmortem_id", "id"],
            "nested_key": "postmortems",
            "snapshot_key": "postmortems",
        },
        "evolution_decisions": {
            "env": "PANTHEON_BFF_EVOLUTION_DECISION_STORE",
            "dirs": ("EVOLUTION_DATA_DIR",),
            "filenames": ("decisions.json",),
            "keys": ["decision_id", "id"],
            "snapshot_key": "evolution_decisions",
        },
        "telemetry_summaries": {
            "env": "PANTHEON_BFF_TELEMETRY_SUMMARY_STORE",
            "dirs": (),
            "filenames": (),
            "keys": ["runtime_id", "id"],
            "snapshot_key": "telemetry_summaries",
        },
        "telemetry_performance": {
            "env": "PANTHEON_BFF_TELEMETRY_PERFORMANCE_STORE",
            "dirs": (),
            "filenames": (),
            "keys": ["artifact_id", "id"],
            "snapshot_key": "telemetry_performance",
        },
        "paper_live_drift_reports": {
            "env": "PANTHEON_BFF_PAPER_LIVE_DRIFT_STORE",
            "dirs": (),
            "filenames": (),
            "keys": ["runtime_id", "id"],
            "snapshot_key": "paper_live_drift_reports",
        },
        "lineage_edges": {
            "env": "PANTHEON_BFF_LINEAGE_EDGE_STORE",
            "dirs": (),
            "filenames": (),
            "keys": ["id"],
            "snapshot_key": "lineage_edges",
        },
        "inspiration_graphs": {
            "env": "PANTHEON_BFF_INSPIRATION_GRAPH_STORE",
            "dirs": (),
            "filenames": (),
            "keys": ["artifact_id", "id"],
            "snapshot_key": "inspiration_graphs",
        },
        "research_tickets": {
            "env": "PANTHEON_BFF_RESEARCH_TICKET_STORE",
            "dirs": (),
            "filenames": (),
            "keys": ["ticket_id", "id"],
            "snapshot_key": "research_tickets",
        },
        "research_experiments": {
            "env": "PANTHEON_BFF_RESEARCH_EXPERIMENT_STORE",
            "dirs": (),
            "filenames": (),
            "keys": ["experiment_id", "id"],
            "snapshot_key": "research_experiments",
        },
        "institutional_memory_entries": {
            "env": "PANTHEON_BFF_INSTITUTIONAL_MEMORY_STORE",
            "dirs": ("PANTHEON_MEMORY_DATA_DIR",),
            "filenames": ("institutional_memory_entries.json", "institutional_memory.json"),
            "keys": ["entry_id", "id"],
            "snapshot_key": "institutional_memory_entries",
        },
        "research_search_documents": {
            "env": "PANTHEON_BFF_RESEARCH_SEARCH_DOCUMENT_STORE",
            "dirs": (),
            "filenames": (),
            "keys": ["result_id", "id"],
            "snapshot_key": "research_search_documents",
        },
        "research_search_index": {
            "env": "PANTHEON_BFF_RESEARCH_SEARCH_INDEX_STORE",
            "dirs": (),
            "filenames": (),
            "keys": ["adapter_id", "id"],
            "snapshot_key": "research_search_index",
        },
        "consult_requests": {
            "env": "PANTHEON_BFF_CONSULT_REQUEST_STORE",
            "dirs": (),
            "filenames": (),
            "keys": ["request_id", "id"],
            "snapshot_key": "consult_requests",
        },
    }

    def __init__(self, *, snapshot_path: Optional[Path] = None) -> None:
        self._cache: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self._cache_meta: Dict[str, tuple[str, int]] = {}
        self._snapshot_path = snapshot_path or _default_bff_snapshot_path()

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
        if path is not None and path.exists():
            stat = path.stat().st_mtime_ns
            cache_key = str(path)
            if self._cache_meta.get(dataset) == (cache_key, stat):
                return True, self._cache.get(dataset, {})

            text = path.read_text(encoding="utf-8").strip()
            payload = json.loads(text) if text else {}
            nested_key = self._DATASETS[dataset].get("nested_key")
            if nested_key and isinstance(payload, dict):
                payload = payload.get(str(nested_key), {})
            normalized = _normalize_records(payload, self._DATASETS[dataset]["keys"])
            self._cache[dataset] = normalized
            self._cache_meta[dataset] = (cache_key, stat)
            return True, normalized

        snapshot_key = self._DATASETS[dataset].get("snapshot_key")
        if snapshot_key:
            available, normalized, cache_meta = _load_snapshot_dataset(
                self._snapshot_path,
                str(snapshot_key),
                self._DATASETS[dataset]["keys"],
            )
            if available:
                if self._cache_meta.get(dataset) != cache_meta:
                    self._cache[dataset] = normalized
                    self._cache_meta[dataset] = cache_meta
                return True, self._cache.get(dataset, normalized)

        return False, {}

    def list_records(self, dataset: str) -> tuple[bool, List[Dict[str, Any]]]:
        available, records = self._load_dataset(dataset)
        return available, list(records.values())

    def record(self, dataset: str, record_id: Optional[str]) -> tuple[bool, Optional[Dict[str, Any]]]:
        if not record_id:
            available, _ = self._load_dataset(dataset)
            return available, None
        available, records = self._load_dataset(dataset)
        return available, records.get(str(record_id))

    def write_records(self, dataset: str, records: Dict[str, Dict[str, Any]]) -> bool:
        path = self._resolve_path(dataset)
        if path is None:
            return False

        normalized = {
            str(key): json.loads(json.dumps(value))
            for key, value in records.items()
            if isinstance(value, dict)
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(normalized, indent=2, ensure_ascii=True),
            encoding="utf-8",
        )
        self._cache[dataset] = normalized
        self._cache_meta[dataset] = (str(path), path.stat().st_mtime_ns)
        return True


def _default_read_data() -> Dict[str, Any]:
    return {
        "deployment_plans": {
            "plan-F-042": {
                "id": "plan-F-042",
                "plan_id": "plan-F-042",
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
            },
            "appr-dec-c5a9f11e": {
                "id": "appr-dec-c5a9f11e",
                "outcome": None,
                "state": "under_review",
                "reviewer": "reviewer-001",
                "actor_role": "reviewer",
                "decided_at": None,
                "risk_level": "medium",
                "rationale": "Medium-risk freeze proposal is awaiting final approval.",
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
            "p-macro-observer": {
                "id": "p-macro-observer",
                "name": "Macro Observer",
                "lifecycle_state": "active",
                "mandate": "macro_regime_review",
                "strategy_family": "macro",
                "created_at": "2026-02-20T00:00:00Z",
                "last_active_at": "2026-04-19T17:08:00Z",
            },
            "p-execution-lead": {
                "id": "p-execution-lead",
                "name": "Execution Lead",
                "lifecycle_state": "active",
                "mandate": "execution_quality",
                "strategy_family": "execution",
                "created_at": "2026-02-24T00:00:00Z",
                "last_active_at": "2026-04-19T17:09:20Z",
            },
            "p-compliance-sponsor": {
                "id": "p-compliance-sponsor",
                "name": "Compliance Sponsor",
                "lifecycle_state": "active",
                "mandate": "governance_sponsorship",
                "strategy_family": "governance",
                "created_at": "2026-02-28T00:00:00Z",
                "last_active_at": "2026-04-19T17:11:00Z",
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
            "trn-20260419-001": {
                "id": "trn-20260419-001",
                "session_id": "trn-20260419-001",
                "persona_id": "persona-alpha",
                "session_type": "trainer",
                "opened_by": "operator-hedging-desk",
                "status": "active",
                "started_at": "2026-04-19T19:30:00Z",
                "ended_at": None,
                "objective": "Tighten event-window response and reduce premature signal reversals during macro surprise sessions.",
                "context_refs": [
                    {
                        "type": "research_ticket",
                        "id": "rt-20260419-007",
                    },
                    {
                        "type": "memory_entry",
                        "id": "mem-8f3c6d45-7d61-4c61-a0c6-3c5e8d1740f1",
                    },
                ],
                "actor_context": {
                    "persona_display_name": "Alpha Persona",
                    "persona_role_context": "systematic momentum coach",
                },
                "events": [
                    {
                        "event_id": "tevt-20260419-001",
                        "session_id": "trn-20260419-001",
                        "actor": "operator",
                        "message_body": "Focus on macro surprise windows where the current policy reverses within the first three bars after a data release.",
                        "emitted_at": "2026-04-19T19:30:11Z",
                        "sequence_number": 1,
                        "outcome_signal": None,
                    },
                    {
                        "event_id": "tevt-20260419-002",
                        "session_id": "trn-20260419-001",
                        "actor": "persona",
                        "message_body": "I am overweighting immediate reversal probability after event spikes. I can hold the initial directional bias longer when volatility expands with breadth confirmation.",
                        "emitted_at": "2026-04-19T19:31:04Z",
                        "sequence_number": 2,
                        "outcome_signal": "acknowledged-adjustment",
                    },
                    {
                        "event_id": "tevt-20260419-003",
                        "session_id": "trn-20260419-001",
                        "actor": "operator",
                        "message_body": "Constrain reversal sensitivity during the first five minutes unless the spread regime also deteriorates.",
                        "emitted_at": "2026-04-19T19:37:40Z",
                        "sequence_number": 3,
                        "outcome_signal": "candidate-adjustment-ready",
                    },
                ],
                "topic": "macro surprise window coaching",
                "outcomes": ["candidate-adjustment-ready"],
            },
            "trn-20260418-003": {
                "id": "trn-20260418-003",
                "session_id": "trn-20260418-003",
                "persona_id": "persona-alpha",
                "session_type": "trainer",
                "opened_by": "operator-risk-desk",
                "status": "completed",
                "started_at": "2026-04-18T08:00:00Z",
                "ended_at": "2026-04-18T08:42:00Z",
                "objective": "Reduce regime-switch whipsaw sensitivity in drawdown containment mode.",
                "context_refs": [],
                "actor_context": {
                    "persona_display_name": "Alpha Persona",
                    "persona_role_context": "systematic momentum coach",
                },
                "events": [
                    {
                        "event_id": "tevt-20260418-001",
                        "session_id": "trn-20260418-003",
                        "actor": "operator",
                        "message_body": "Focus on drawdown containment and reduce sensitivity to short-lived regime flips.",
                        "emitted_at": "2026-04-18T08:00:12Z",
                        "sequence_number": 1,
                        "outcome_signal": None,
                    },
                    {
                        "event_id": "tevt-20260418-002",
                        "session_id": "trn-20260418-003",
                        "actor": "persona",
                        "message_body": "I can widen confirmation requirements before reversing the posture in containment mode.",
                        "emitted_at": "2026-04-18T08:42:00Z",
                        "sequence_number": 2,
                        "outcome_signal": "teaching-complete",
                    },
                ],
                "topic": "drawdown containment coaching",
                "outcomes": ["teaching-complete"],
            },
        },
        "trainer_previews": {
            "trn-20260419-001": {
                "session_id": "trn-20260419-001",
                "latest_eval_id": "teval-20260419-014",
                "evaluations": {
                    "teval-20260419-014": {
                        "session_id": "trn-20260419-001",
                        "status": "complete",
                        "eval_id": "teval-20260419-014",
                        "baseline_snapshot_at": "2026-04-19T19:43:30Z",
                        "candidate_snapshot_at": "2026-04-19T19:48:00Z",
                        "control_diff": [
                            {
                                "control_id": "ctrl-reversal-threshold",
                                "parameter_key": "reversal_threshold",
                                "display_label": "Reversal Threshold",
                                "previous_value": 0.65,
                                "new_value": 0.9,
                                "unit": "score",
                                "last_modified_at": "2026-04-19T19:45:12Z",
                            },
                            {
                                "control_id": "ctrl-hold-bars",
                                "parameter_key": "minimum_hold_bars",
                                "display_label": "Minimum Hold Bars",
                                "previous_value": 3,
                                "new_value": 4,
                                "unit": "bars",
                                "last_modified_at": "2026-04-19T19:43:30Z",
                            },
                        ],
                        "metric_delta": [
                            {
                                "metric_key": "event_window_reversal_rate",
                                "display_label": "Event Window Reversal Rate",
                                "baseline_value": 0.31,
                                "candidate_value": 0.22,
                                "delta": -0.09,
                                "delta_pct": -29.03,
                                "unit": "ratio",
                                "direction": "improved",
                            },
                            {
                                "metric_key": "sponsor_alignment_score",
                                "display_label": "Sponsor Alignment Score",
                                "baseline_value": 0.74,
                                "candidate_value": 0.81,
                                "delta": 0.07,
                                "delta_pct": 9.46,
                                "unit": "score",
                                "direction": "improved",
                            },
                            {
                                "metric_key": "latency_to_recover_after_false_break",
                                "display_label": "False-Break Recovery Latency",
                                "baseline_value": 3.2,
                                "candidate_value": 3.6,
                                "delta": 0.4,
                                "delta_pct": 12.5,
                                "unit": "bars",
                                "direction": "regressed",
                            },
                        ],
                        "warnings": [
                            {
                                "warning_id": "warn-preview-20260419-001",
                                "warning_code": "upper_bound_pressure",
                                "level": "high",
                                "parameter_key": "reversal_threshold",
                                "metric_key": "latency_to_recover_after_false_break",
                                "message": "The candidate pushes reversal sensitivity near the upper bound and increases false-break recovery latency.",
                                "impact_summary": "Recovery remains slower in thin-liquidity regimes even though immediate reversal rate improves.",
                            },
                            {
                                "warning_id": "warn-preview-20260419-002",
                                "warning_code": "limited_regime_coverage",
                                "level": "medium",
                                "parameter_key": None,
                                "metric_key": "sponsor_alignment_score",
                                "message": "Rapid-eval coverage is directional only for the last two FOMC surprise windows.",
                                "impact_summary": "Preview is useful for operator review, but the sample is not broad enough for unattended promotion.",
                            },
                            {
                                "warning_id": "warn-preview-20260419-003",
                                "warning_code": "spread_gate_note",
                                "level": "informational",
                                "parameter_key": "spread_regime_gate",
                                "metric_key": None,
                                "message": "Spread regime gate remains unchanged from the current baseline.",
                                "impact_summary": "No additional gating was introduced for stable spread sessions.",
                            },
                        ],
                        "warning_count_by_level": {
                            "critical": 0,
                            "high": 1,
                            "medium": 1,
                            "informational": 1,
                        },
                        "preview_quality": "directional_only",
                        "allowedActions": {
                            "canRefreshPreview": True,
                        },
                        "polling": {
                            "enabled": False,
                            "poll_interval_ms": 3000,
                            "max_wait_ms": 45000,
                            "deadline_at": None,
                        },
                        "degraded_copy": None,
                        "meta": {
                            "snapshot_at": "2026-04-19T19:48:04Z",
                            "surfaces": {
                                "trainer_preview": "ok",
                            },
                        },
                    },
                    "teval-20260419-015": {
                        "session_id": "trn-20260419-001",
                        "status": "pending",
                        "eval_id": "teval-20260419-015",
                        "baseline_snapshot_at": "2026-04-19T19:43:30Z",
                        "candidate_snapshot_at": "2026-04-19T19:50:00Z",
                        "control_diff": [
                            {
                                "control_id": "ctrl-reversal-threshold",
                                "parameter_key": "reversal_threshold",
                                "display_label": "Reversal Threshold",
                                "previous_value": 0.65,
                                "new_value": 0.85,
                                "unit": "score",
                                "last_modified_at": "2026-04-19T19:50:00Z",
                            },
                        ],
                        "metric_delta": [],
                        "warnings": [],
                        "warning_count_by_level": {
                            "critical": 0,
                            "high": 0,
                            "medium": 0,
                            "informational": 0,
                        },
                        "preview_quality": "directional_only",
                        "allowedActions": {
                            "canRefreshPreview": False,
                        },
                        "polling": {
                            "enabled": True,
                            "poll_interval_ms": 3000,
                            "max_wait_ms": 45000,
                            "deadline_at": "2026-04-19T19:50:45Z",
                        },
                        "degraded_copy": {
                            "title": "Trainer preview is still running",
                            "body": "Pantheon is evaluating the current trainer candidate. Keep the compare surface open and poll again after the published interval.",
                        },
                        "meta": {
                            "snapshot_at": "2026-04-19T19:50:03Z",
                            "surfaces": {
                                "trainer_preview": "ok",
                            },
                        },
                    },
                    "teval-20260419-016": {
                        "session_id": "trn-20260419-001",
                        "status": "failed",
                        "eval_id": "teval-20260419-016",
                        "baseline_snapshot_at": "2026-04-19T19:43:30Z",
                        "candidate_snapshot_at": "2026-04-19T19:51:10Z",
                        "control_diff": [
                            {
                                "control_id": "ctrl-hold-bars",
                                "parameter_key": "minimum_hold_bars",
                                "display_label": "Minimum Hold Bars",
                                "previous_value": 3,
                                "new_value": 5,
                                "unit": "bars",
                                "last_modified_at": "2026-04-19T19:51:10Z",
                            },
                        ],
                        "metric_delta": [],
                        "warnings": [],
                        "warning_count_by_level": {
                            "critical": 0,
                            "high": 0,
                            "medium": 0,
                            "informational": 0,
                        },
                        "preview_quality": "insufficient_data",
                        "allowedActions": {
                            "canRefreshPreview": False,
                        },
                        "polling": {
                            "enabled": False,
                            "poll_interval_ms": 3000,
                            "max_wait_ms": 45000,
                            "deadline_at": None,
                        },
                        "degraded_copy": {
                            "title": "Trainer preview could not complete",
                            "body": "Pantheon could not finish the rapid-eval for this candidate. Review the current control diff, then retry the preview when the compare surface is healthy.",
                        },
                        "meta": {
                            "snapshot_at": "2026-04-19T19:51:22Z",
                            "surfaces": {
                                "trainer_preview": "degraded",
                            },
                        },
                    },
                },
            },
            "trn-20260418-003": {
                "session_id": "trn-20260418-003",
                "latest_eval_id": None,
                "evaluations": {},
                "preview": {
                    "session_id": "trn-20260418-003",
                    "status": "preview_unavailable",
                    "eval_id": None,
                    "baseline_snapshot_at": "2026-04-18T08:35:00Z",
                    "candidate_snapshot_at": "2026-04-18T08:40:00Z",
                    "control_diff": [
                        {
                            "control_id": "ctrl-reversal-threshold",
                            "parameter_key": "reversal_threshold",
                            "display_label": "Reversal Threshold",
                            "previous_value": 0.65,
                            "new_value": 0.8,
                            "unit": "score",
                            "last_modified_at": "2026-04-18T08:40:00Z",
                        },
                    ],
                    "metric_delta": [],
                    "warnings": [],
                    "warning_count_by_level": {
                        "critical": 0,
                        "high": 0,
                        "medium": 0,
                        "informational": 0,
                    },
                    "preview_quality": "not_available",
                    "allowedActions": {
                        "canRefreshPreview": False,
                    },
                    "polling": {
                        "enabled": False,
                        "poll_interval_ms": 3000,
                        "max_wait_ms": 45000,
                        "deadline_at": None,
                    },
                    "degraded_copy": {
                        "title": "Trainer preview is temporarily unavailable",
                        "body": "Pantheon cannot serve rapid-eval results for the trainer compare surface right now. Control changes remain visible, but before/after metrics are temporarily unavailable.",
                    },
                    "meta": {
                        "snapshot_at": "2026-04-18T08:40:07Z",
                        "surfaces": {
                            "trainer_preview": "degraded",
                        },
                    },
                },
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
        "registry_entries": {
            "artifact-041": {
                "registry_id": "artifact-041",
                "artifact_id": "artifact-041",
                "artifact_type": "strategy",
                "version": "v2.0.0",
                "artifact_version": "v2.0.0",
            },
            "artifact-042": {
                "registry_id": "artifact-042",
                "artifact_id": "artifact-042",
                "artifact_type": "strategy",
                "version": "v2.1.0",
                "artifact_version": "v2.1.0",
            },
            "artifact-043": {
                "registry_id": "artifact-043",
                "artifact_id": "artifact-043",
                "artifact_type": "strategy",
                "version": "v2.2.0",
                "artifact_version": "v2.2.0",
            },
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
            "last_confirmed_at": "2026-04-11T12:00:00Z",
            "last_triggered_at": None,
            "active_commands": [],
            "secondary_path_available": True,
            "safe_mode_status": "off",
            "status": "armed",
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
        "rollback_reviews": {
            "rollback-rb-001": {
                "rollback_id": "rollback-rb-001",
                "target_plan_id": "plan-dp-000",
                "trigger_reason": "Automated risk trigger: max_drawdown threshold breached during paper trading window.",
                "requested_at": "2026-04-16T09:30:00Z",
                "requested_by": "risk-monitor",
                "rollback_scope": "partial",
                "affected_persona_count": 2,
                "affected_binding_count": 3,
                "target_stage": "paper",
                "position_impact": [
                    {
                        "binding_id": "binding-001",
                        "persona_id": "persona-alpha",
                        "current_stage": "paper",
                        "target_stage": "paper",
                        "position_impact_summary": "Open long position of 4% portfolio weight will be closed before rollback. No live positions affected.",
                        "position_data_stale": False,
                    },
                    {
                        "binding_id": "binding-002",
                        "persona_id": "persona-beta",
                        "current_stage": "paper",
                        "target_stage": "paper",
                        "position_impact_summary": "No open positions; rollback is position-neutral for this binding.",
                        "position_data_stale": False,
                    },
                    {
                        "binding_id": "binding-003",
                        "persona_id": "persona-alpha",
                        "current_stage": "paper",
                        "target_stage": "paper",
                        "position_impact_summary": None,
                        "position_data_stale": True,
                    },
                ],
                "affected_bindings": [
                    {
                        "binding_id": "binding-001",
                        "persona_id": "persona-alpha",
                        "capital_pool_id": "pool-002",
                        "current_stage": "paper",
                    },
                    {
                        "binding_id": "binding-002",
                        "persona_id": "persona-beta",
                        "capital_pool_id": "pool-001",
                        "current_stage": "paper",
                    },
                    {
                        "binding_id": "binding-003",
                        "persona_id": "persona-alpha",
                        "capital_pool_id": "pool-002",
                        "current_stage": "paper",
                    },
                ],
                "trigger_evidence": {
                    "trigger_reason": "Automated risk trigger: max_drawdown threshold breached during paper trading window.",
                    "evidence_refs": [
                        {"ref_id": "ev-rb-001", "type": "TelemetryAlert", "url": None},
                        {"ref_id": "ev-rb-002", "type": "RiskControlEvent", "url": None},
                    ],
                    "linked_incident_id": None,
                },
                "allowedActions": {
                    "canApproveRollback": False,
                    "canRejectRollback": True,
                },
                "meta": {
                    "snapshot_at": "2026-04-16T10:00:00Z",
                    "surfaces": {
                        "position_data": {"status": "degraded"},
                        "rollback_review": {"status": "ok"},
                        "allowedActions": {"status": "ok"},
                    },
                },
            },
        },
        "governance_audit_events": [
            {
                "entry_id": "audit-001",
                "actor": "operator-jane",
                "action_type": "ApproveDecision",
                "target_type": "ApprovalDecision",
                "target_id": "appr-001",
                "timestamp": "2026-04-16T10:05:00Z",
                "outcome": "success",
                "audit_context": {
                    "reason": "Risk review completed; all evidence within acceptable bounds.",
                },
                "evidence_refs": [
                    {"ref_id": "ev-101", "type": "BacktestResult", "url": None},
                ],
            },
            {
                "entry_id": "audit-002",
                "actor": "operator-jane",
                "action_type": "ForwardToApprovalQueue",
                "target_type": "GovernanceReviewItem",
                "target_id": "gov-review-001",
                "timestamp": "2026-04-16T09:58:00Z",
                "outcome": "success",
                "audit_context": {
                    "reason": "Review complete; forwarding to approval.",
                },
                "evidence_refs": [],
            },
            {
                "entry_id": "audit-003",
                "actor": "risk-monitor",
                "action_type": "EscalateGovernanceItem",
                "target_type": "GovernanceReviewItem",
                "target_id": "gov-review-002",
                "timestamp": "2026-04-16T09:45:00Z",
                "outcome": "escalated",
                "audit_context": {
                    "reason": None,
                },
                "evidence_refs": [
                    {"ref_id": "ev-103", "type": "EvolutionDecision", "url": None},
                ],
            },
            {
                "entry_id": "audit-004",
                "actor": "operator-bob",
                "action_type": "RejectRollback",
                "target_type": "Rollback",
                "target_id": "rollback-rb-001",
                "timestamp": "2026-04-16T09:40:00Z",
                "outcome": "success",
                "audit_context": {
                    "reason": "Position data is stale; cannot safely approve rollback at this time.",
                },
                "evidence_refs": [],
            },
            {
                "entry_id": "audit-005",
                "actor": "operator-jane",
                "action_type": "RequestGovernanceChanges",
                "target_type": "GovernanceReviewItem",
                "target_id": "gov-review-003",
                "timestamp": "2026-04-16T09:20:00Z",
                "outcome": "success",
                "audit_context": {
                    "reason": "Capital pool reference needs correction before approval.",
                },
                "evidence_refs": [],
            },
        ],
        "governance_review_queue_items": {
            "gov-review-001": {
                "item_id": "gov-review-001",
                "item_type": "DeploymentPlan",
                "risk_level": "medium",
                "status": "pending",
                "submitted_at": "2026-04-14T06:15:00Z",
                "submitted_by": "orchestrator",
                "governance_outcome": "pending",
                "allowedActions": {
                    "canReview": True,
                    "canForwardToApproval": True,
                    "canRequestChanges": True,
                    "canEscalate": False,
                },
                "review_summary": {
                    "risk_assessment": "Medium risk - parameter drift within acceptable bounds; no open severity-1 or severity-2 incidents.",
                    "evidence_refs": [
                        {"ref_id": "ev-001", "type": "IncidentReport", "url": None},
                        {"ref_id": "ev-002", "type": "BacktestResult", "url": None},
                    ],
                    "linked_approval_decision_id": None,
                },
            },
            "gov-review-002": {
                "item_id": "gov-review-002",
                "item_type": "EvolutionProposal",
                "risk_level": "high",
                "status": "escalated",
                "submitted_at": "2026-04-14T05:45:00Z",
                "submitted_by": "orchestrator",
                "governance_outcome": "escalated",
                "allowedActions": {
                    "canReview": False,
                    "canForwardToApproval": False,
                    "canRequestChanges": False,
                    "canEscalate": False,
                },
                "review_summary": {
                    "risk_assessment": "High risk - escalated due to open severity-2 incident; no further routing CTAs available until resolved.",
                    "evidence_refs": [
                        {"ref_id": "ev-003", "type": "IncidentReport", "url": None},
                    ],
                    "linked_approval_decision_id": None,
                },
            },
            "gov-review-003": {
                "item_id": "gov-review-003",
                "item_type": "PersonaBinding",
                "risk_level": "low",
                "status": "in_review",
                "submitted_at": "2026-04-14T07:00:00Z",
                "submitted_by": "orchestrator",
                "governance_outcome": "pending",
                "allowedActions": {
                    "canReview": True,
                    "canForwardToApproval": True,
                    "canRequestChanges": True,
                    "canEscalate": True,
                },
                "review_summary": {
                    "risk_assessment": "Low risk - new persona binding within established capital pool; no open incidents.",
                    "evidence_refs": [],
                    "linked_approval_decision_id": None,
                },
            },
        },
        "approval_queue_items": {
            "appr-001": {
                "decision_id": "appr-001",
                "decision_type": "DeploymentPlan",
                "risk_level": "medium",
                "submitted_at": "2026-04-16T08:15:00Z",
                "submitted_by": "governance-review-queue",
                "decision_state": "pending",
                "allowedActions": {
                    "canApprove": True,
                    "canReject": True,
                    "canRequestRevision": True,
                },
                "decision_context": {
                    "risk_summary": "Medium risk — parameter drift within acceptable bounds; no open severity-1 or severity-2 incidents. Review queue forwarded after passing risk threshold check.",
                    "evidence_refs": [
                        {"ref_id": "ev-101", "type": "BacktestResult", "url": None},
                        {"ref_id": "ev-102", "type": "IncidentReport", "url": None},
                    ],
                    "governance_chain": {
                        "linked_review_item_id": "gov-review-001",
                    },
                    "required_approvals": 1,
                },
            },
            "appr-002": {
                "decision_id": "appr-002",
                "decision_type": "PersonaBinding",
                "risk_level": "low",
                "submitted_at": "2026-04-16T09:00:00Z",
                "submitted_by": "governance-review-queue",
                "decision_state": "pending",
                "allowedActions": {
                    "canApprove": True,
                    "canReject": True,
                    "canRequestRevision": False,
                },
                "decision_context": {
                    "risk_summary": "Low risk — new persona binding within established capital pool; no open incidents.",
                    "evidence_refs": [],
                    "governance_chain": {
                        "linked_review_item_id": "gov-review-003",
                    },
                    "required_approvals": 1,
                },
            },
            "appr-003": {
                "decision_id": "appr-003",
                "decision_type": "EvolutionProposal",
                "risk_level": "high",
                "submitted_at": "2026-04-16T07:30:00Z",
                "submitted_by": "governance-review-queue",
                "decision_state": "in_review",
                "allowedActions": {
                    "canApprove": False,
                    "canReject": False,
                    "canRequestRevision": False,
                },
                "decision_context": {
                    "risk_summary": "High risk — evolution proposal involves parameter changes beyond threshold bounds; additional review in progress.",
                    "evidence_refs": [
                        {"ref_id": "ev-103", "type": "EvolutionDecision", "url": None},
                    ],
                    "governance_chain": {
                        "linked_review_item_id": "gov-review-002",
                    },
                    "required_approvals": 2,
                },
            },
        },
        "deployment_diffs": {
            "plan-dp-001": {
                "plan_id": "plan-dp-001",
                "artifact_id": "artifact-abc123",
                "stage": "paper",
                "submitted_at": "2026-04-16T08:00:00Z",
                "submitted_by": "orchestrator",
                "previous_plan_id": "plan-dp-000",
                "first_deployment": False,
                "changes": [
                    {
                        "field_path": "parameters.max_drawdown",
                        "previous_value": "0.08",
                        "current_value": "0.10",
                        "change_reason": "Risk policy updated to allow 10% max drawdown for this persona class after governance review.",
                        "change_category": "parameters",
                        "risk_tier": "medium",
                    },
                    {
                        "field_path": "parameters.position_size_limit",
                        "previous_value": "0.05",
                        "current_value": "0.04",
                        "change_reason": "Position size limit reduced to align with updated capital pool allocation.",
                        "change_category": "capital_allocation",
                        "risk_tier": "low",
                    },
                    {
                        "field_path": "bindings[0].capital_pool_id",
                        "previous_value": "pool-001",
                        "current_value": "pool-002",
                        "change_reason": "Binding reassigned to new capital pool after pool-001 was retired.",
                        "change_category": "bindings",
                        "risk_tier": "high",
                    },
                    {
                        "field_path": "risk_controls.stop_loss_threshold",
                        "previous_value": "0.03",
                        "current_value": "0.025",
                        "change_reason": "Stop-loss threshold tightened following post-incident review recommendations.",
                        "change_category": "risk_controls",
                        "risk_tier": "medium",
                    },
                ],
                "change_summary": {
                    "total_changes": 4,
                    "by_category": {
                        "parameters": {"count": 1, "highest_risk_tier": "medium"},
                        "capital_allocation": {"count": 1, "highest_risk_tier": "low"},
                        "bindings": {"count": 1, "highest_risk_tier": "high"},
                        "risk_controls": {"count": 1, "highest_risk_tier": "medium"},
                        "stage_transition": {"count": 0, "highest_risk_tier": None},
                    },
                },
                "allowedActions": {
                    "canProceedToApproval": True,
                    "canEscalateDiff": True,
                },
            },
            "plan-dp-002": {
                "plan_id": "plan-dp-002",
                "artifact_id": "artifact-def456",
                "stage": "paper",
                "submitted_at": "2026-04-16T11:20:00Z",
                "submitted_by": "orchestrator",
                "previous_plan_id": None,
                "first_deployment": True,
                "changes": [],
                "change_summary": {
                    "total_changes": 0,
                    "by_category": {
                        "parameters": {"count": 0, "highest_risk_tier": None},
                        "capital_allocation": {"count": 0, "highest_risk_tier": None},
                        "bindings": {"count": 0, "highest_risk_tier": None},
                        "risk_controls": {"count": 0, "highest_risk_tier": None},
                        "stage_transition": {"count": 0, "highest_risk_tier": None},
                    },
                },
                "allowedActions": {
                    "canProceedToApproval": False,
                    "canEscalateDiff": True,
                },
            },
        },
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
        "inspiration_graphs": {
            "artifact-042": {
                "artifact_id": "artifact-042",
                "inspiration_edges": [
                    {
                        "source_artifact_id": "artifact-041",
                        "relationship_type": "derived_from",
                        "influence_weight": 0.85,
                    },
                    {
                        "source_artifact_id": "artifact-039",
                        "relationship_type": "strategy_applied",
                        "influence_weight": 0.60,
                    },
                    {
                        "source_artifact_id": "artifact-038",
                        "relationship_type": "inspired_by",
                        "influence_weight": 0.40,
                    },
                ],
                "strategy_tags": [
                    "momentum-alpha",
                    "low-volatility",
                    "sector-rotation",
                ],
                "snapshot_at": "2026-04-19T03:00:00Z",
                "surface_state": "fresh",
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
            "cs-20260419-081": {
                "session_id": "cs-20260419-081",
                "persona_id": "persona-alpha",
                "session_type": "consult",
                "status": "active",
                "started_at": "2026-04-19T17:06:00Z",
                "ended_at": None,
                "capability_snapshot_id": "cap-001",
                "trace_id": "trace-cs-20260419-081",
                "request_id": "cr-20260419-014",
                "context_bundle_ref": "workspace://consultation-context/cs-20260419-081",
                "task_ref": None,
                "runtime_binding_id": "runtime-042",
                "deployment_stage": "paper",
                "capital_pool_id": "pool-main",
                "metadata": {
                    "consultation": {
                        "consultation_type": "risk_review",
                        "requester_session_id": "cs-20260419-081",
                        "responder_session_ids": [],
                        "committee_session_ids": [
                            "cm-20260419-081-001",
                            "cm-20260419-081-002",
                            "cm-20260419-081-003",
                        ],
                        "consult_policy_ref": "cp-alpha",
                        "trigger_rule": "macro_regime_shift",
                        "required_reviewers": 0,
                        "required_committees": ["committee-regime-risk"],
                        "forbidden_solo_actions": ["approve_live_deployment"],
                        "actual_reviewers": 0,
                        "outcome": "pending",
                        "rationale_ref": "workspace://consultation-rationales/cs-20260419-081",
                        "evidence_refs": [
                            {
                                "id": "telemetry-vol-spike-20260419",
                                "type": "evidence_link",
                                "evidence_type": "telemetry",
                                "artifact_ref": "artifact-042",
                                "description": "Volatility spike - 2026-04-19",
                                "link": "/telemetry/events/telemetry-vol-spike-20260419",
                            },
                            {
                                "id": "dp-20260419-014",
                                "type": "evidence_link",
                                "evidence_type": "deployment_plan",
                                "artifact_ref": "dp-20260419-014",
                                "description": "Deployment plan dp-20260419-014",
                                "link": "/deployments/plans/dp-20260419-014",
                            },
                        ],
                        "dissent_refs": [
                            "workspace://consultation-dissent/cs-20260419-081/execution-lead"
                        ],
                        "escalation_path": "committee_override",
                        "committee_ref": "committee-regime-risk-20260419-081",
                        "quorum_state": "quorum_met",
                        "consensus_state": "sponsor_required",
                        "committee_started_at": "2026-04-19T17:07:00Z",
                        "committee_surface_state": "ok",
                        "sponsor_session_id": "cm-20260419-081-003",
                        "sponsor_decision": None,
                        "sponsor_decided_at": None,
                        "sponsor_decided_by": None,
                        "escalation_reason": {
                            "trigger_rule": "macro_regime_shift",
                            "forbidden_solo_action": "approve_live_deployment",
                            "escalation_path": "committee_override",
                        },
                        "synthesis_summary": {
                            "outcome": "pending",
                            "rationale_ref": "workspace://consultation-rationales/cs-20260419-081",
                            "evidence_refs": [
                                "telemetry-vol-spike-20260419",
                                "dp-20260419-014",
                            ],
                            "dissent_refs": [
                                "workspace://consultation-dissent/cs-20260419-081/execution-lead"
                            ],
                        },
                    }
                },
            },
            "cm-20260419-081-001": {
                "session_id": "cm-20260419-081-001",
                "persona_id": "p-macro-observer",
                "session_type": "committee",
                "status": "active",
                "started_at": "2026-04-19T17:07:00Z",
                "ended_at": None,
                "capability_snapshot_id": "cap-001",
                "trace_id": "trace-cm-20260419-081-001",
                "request_id": "cr-20260419-014",
                "context_bundle_ref": "workspace://consultation-context/cs-20260419-081",
                "task_ref": None,
                "runtime_binding_id": "runtime-042",
                "deployment_stage": "paper",
                "capital_pool_id": "pool-main",
                "metadata": {
                    "consultation": {
                        "consultation_type": "risk_review",
                        "root_session_id": "cs-20260419-081",
                        "committee_ref": "committee-regime-risk-20260419-081",
                        "participant_status": "voted",
                        "outcome_signal": "approved",
                        "role": "committee_participant",
                        "rationale_ref": "workspace://consultation-rationales/cs-20260419-081/macro-observer",
                    }
                },
            },
            "cm-20260419-081-002": {
                "session_id": "cm-20260419-081-002",
                "persona_id": "p-execution-lead",
                "session_type": "committee",
                "status": "active",
                "started_at": "2026-04-19T17:07:30Z",
                "ended_at": None,
                "capability_snapshot_id": "cap-001",
                "trace_id": "trace-cm-20260419-081-002",
                "request_id": "cr-20260419-014",
                "context_bundle_ref": "workspace://consultation-context/cs-20260419-081",
                "task_ref": None,
                "runtime_binding_id": "runtime-042",
                "deployment_stage": "paper",
                "capital_pool_id": "pool-main",
                "metadata": {
                    "consultation": {
                        "consultation_type": "risk_review",
                        "root_session_id": "cs-20260419-081",
                        "committee_ref": "committee-regime-risk-20260419-081",
                        "participant_status": "voted",
                        "outcome_signal": "conditional",
                        "role": "committee_participant",
                        "rationale_ref": "workspace://consultation-dissent/cs-20260419-081/execution-lead",
                    }
                },
            },
            "cm-20260419-081-003": {
                "session_id": "cm-20260419-081-003",
                "persona_id": "p-compliance-sponsor",
                "session_type": "committee",
                "status": "active",
                "started_at": "2026-04-19T17:08:00Z",
                "ended_at": None,
                "capability_snapshot_id": "cap-001",
                "trace_id": "trace-cm-20260419-081-003",
                "request_id": "cr-20260419-014",
                "context_bundle_ref": "workspace://consultation-context/cs-20260419-081",
                "task_ref": None,
                "runtime_binding_id": "runtime-042",
                "deployment_stage": "paper",
                "capital_pool_id": "pool-main",
                "metadata": {
                    "consultation": {
                        "consultation_type": "risk_review",
                        "root_session_id": "cs-20260419-081",
                        "committee_ref": "committee-regime-risk-20260419-081",
                        "participant_status": "active",
                        "outcome_signal": None,
                        "role": "sponsor",
                        "rationale_ref": "workspace://consultation-rationales/cs-20260419-081/sponsor",
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
        "paper_live_drift_reports": {
            "runtime-042": {
                "runtime_id": "runtime-042",
                "artifact_id": "artifact-042",
                "artifact_version": "v2.1.0",
                "plan_id": "plan-F-042",
                "paper_baseline": {
                    "captured_at": "2026-04-09T16:00:00Z",
                    "deployment_stage": "paper",
                    "window": "24h",
                    "metrics": {
                        "pnl": -0.04,
                        "max_drawdown": 0.06,
                        "fill_rate": 0.97,
                        "avg_slippage_bps": 2.4,
                        "turnover": 0.32,
                    },
                },
                "observed_state": {
                    "deployment_stage": "live",
                    "runtime_status": "running",
                    "observed_at": "2026-04-10T15:00:00Z",
                    "metrics": {
                        "pnl": -0.12,
                        "drawdown": 0.125,
                        "fill_rate": 0.94,
                        "avg_slippage_bps": 3.2,
                        "turnover": 0.41,
                    },
                },
                "drift_groups": [
                    {
                        "group_id": "execution",
                        "label": "Execution",
                        "status": "watch",
                        "metrics": [
                            {
                                "metric_id": "fill_rate",
                                "label": "Fill rate",
                                "baseline_value": 0.97,
                                "observed_value": 0.94,
                                "delta": -0.03,
                                "threshold": ">= 0.95",
                                "status": "watch",
                                "unit": "ratio",
                            },
                            {
                                "metric_id": "turnover",
                                "label": "Turnover",
                                "baseline_value": 0.32,
                                "observed_value": 0.41,
                                "delta": 0.09,
                                "threshold": "<= 0.40",
                                "status": "watch",
                                "unit": "ratio",
                            },
                        ],
                    },
                    {
                        "group_id": "exposure",
                        "label": "Exposure",
                        "status": "breached",
                        "metrics": [
                            {
                                "metric_id": "max_drawdown",
                                "label": "Max drawdown",
                                "baseline_value": 0.06,
                                "observed_value": 0.125,
                                "delta": 0.065,
                                "threshold": "<= 0.08",
                                "status": "breached",
                                "unit": "ratio",
                            },
                        ],
                    },
                    {
                        "group_id": "slippage",
                        "label": "Slippage",
                        "status": "breached",
                        "metrics": [
                            {
                                "metric_id": "avg_slippage_bps",
                                "label": "Average slippage",
                                "baseline_value": 2.4,
                                "observed_value": 3.2,
                                "delta": 0.8,
                                "threshold": "<= 3.0",
                                "status": "breached",
                                "unit": "bps",
                            },
                        ],
                    },
                    {
                        "group_id": "risk_signals",
                        "label": "Risk Signals",
                        "status": "breached",
                        "metrics": [
                            {
                                "metric_id": "active_incident_count",
                                "label": "Active incident count",
                                "baseline_value": 0,
                                "observed_value": 1,
                                "delta": 1,
                                "threshold": "== 0",
                                "status": "breached",
                                "unit": "count",
                            },
                            {
                                "metric_id": "safe_mode_active",
                                "label": "Safe mode active",
                                "baseline_value": 0,
                                "observed_value": 1,
                                "delta": 1,
                                "threshold": "== 0",
                                "status": "breached",
                                "unit": "flag",
                            },
                        ],
                    },
                ],
                "threshold_evaluation": {
                    "overall_status": "breached",
                    "summary": "Observed live metrics exceed the published paper baseline envelope; operator review is required.",
                    "breached_metric_ids": [
                        "max_drawdown",
                        "avg_slippage_bps",
                        "active_incident_count",
                        "safe_mode_active",
                    ],
                },
                "evidence_refs": [
                    {
                        "ref_id": "approval-042",
                        "type": "ApprovalDecision",
                        "href": "/api/v1/approval-decisions/approval-042",
                    },
                    {
                        "ref_id": "inc-20260410-001",
                        "type": "IncidentCase",
                        "href": "/api/v1/operator/incident-response/inc-20260410-001",
                    },
                    {
                        "ref_id": "evo-dec-001",
                        "type": "EvolutionDecision",
                        "href": "/api/v1/evolution-decisions/evo-dec-001",
                    },
                    {
                        "ref_id": "drift-report-runtime-042",
                        "type": "drift_report",
                        "href": None,
                    },
                ],
                "recommended_actions": [
                    {
                        "action_id": "open-deployment-review",
                        "label": "Open deployment review",
                        "reason": "Re-verify promotion assumptions against observed live drift.",
                        "target_ref": {
                            "surface_id": "PKT-001",
                            "label": "Open deployment review",
                            "href": "/api/v1/operator/deployment-review/plan-F-042",
                            "target_id": "plan-F-042",
                        },
                    },
                    {
                        "action_id": "open-incident-response",
                        "label": "Open incident response",
                        "reason": "The active incident already tracks the drawdown breach.",
                        "target_ref": {
                            "surface_id": "PKT-002",
                            "label": "Open incident response",
                            "href": "/api/v1/operator/incident-response/inc-20260410-001",
                            "target_id": "inc-20260410-001",
                        },
                    },
                    {
                        "action_id": "open-post-incident-review",
                        "label": "Open post-incident review",
                        "reason": "Review evolution follow-up and drift evidence before another promotion step.",
                        "target_ref": {
                            "surface_id": "PKT-003",
                            "label": "Open post-incident review",
                            "href": "/api/v1/operator/post-incident-review/inc-20260410-001",
                            "target_id": "inc-20260410-001",
                        },
                    },
                ],
            }
        },
        "research_tickets": {
            "rt-20260419-007": {
                "ticket_id": "rt-20260419-007",
                "title": "Evaluate momentum factor decay in high-volatility regime",
                "description": (
                    "Assess whether momentum factors lose predictive power during sustained "
                    "volatility spikes and whether the current strategy's rebalancing windows "
                    "account for this regime shift."
                ),
                "status": "in_progress",
                "priority": "high",
                "owner": "persona-risk-chief",
                "created_at": "2026-04-19T17:10:00Z",
                "updated_at": "2026-04-19T18:30:00Z",
                "closed_at": None,
                "archived_at": None,
                "lifecycle_history": [
                    {
                        "from_status": None,
                        "to_status": "open",
                        "transitioned_at": "2026-04-19T17:10:00Z",
                        "transitioned_by": "persona-risk-chief",
                    },
                    {
                        "from_status": "open",
                        "to_status": "in_progress",
                        "transitioned_at": "2026-04-19T18:30:00Z",
                        "transitioned_by": "persona-risk-chief",
                    },
                ],
                "linked_experiments": ["exp-20260419-012"],
                "linked_artifacts": [],
            },
            "rt-20260418-003": {
                "ticket_id": "rt-20260418-003",
                "title": "Benchmark drawdown recovery across three strategy variants",
                "description": (
                    "Compare recovery time and equity curve shape for the baseline, conservative, "
                    "and accelerated variants after drawdown shocks."
                ),
                "status": "open",
                "priority": "normal",
                "owner": "persona-execution-sponsor",
                "created_at": "2026-04-18T09:00:00Z",
                "updated_at": "2026-04-18T09:00:00Z",
                "closed_at": None,
                "archived_at": None,
                "lifecycle_history": [
                    {
                        "from_status": None,
                        "to_status": "open",
                        "transitioned_at": "2026-04-18T09:00:00Z",
                        "transitioned_by": "persona-execution-sponsor",
                    }
                ],
                "linked_experiments": [],
                "linked_artifacts": [],
            },
            "rt-20260415-001": {
                "ticket_id": "rt-20260415-001",
                "title": "Validate signal quality on macro event windows",
                "description": (
                    "Check whether signal quality deteriorates around scheduled macro events and "
                    "whether the current exclusion windows are sufficient."
                ),
                "status": "closed",
                "priority": "low",
                "owner": "persona-risk-chief",
                "created_at": "2026-04-15T14:20:00Z",
                "updated_at": "2026-04-19T11:00:00Z",
                "closed_at": "2026-04-19T11:00:00Z",
                "archived_at": None,
                "lifecycle_history": [
                    {
                        "from_status": None,
                        "to_status": "open",
                        "transitioned_at": "2026-04-15T14:20:00Z",
                        "transitioned_by": "persona-risk-chief",
                    },
                    {
                        "from_status": "open",
                        "to_status": "in_progress",
                        "transitioned_at": "2026-04-17T09:15:00Z",
                        "transitioned_by": "persona-risk-chief",
                    },
                    {
                        "from_status": "in_progress",
                        "to_status": "closed",
                        "transitioned_at": "2026-04-19T11:00:00Z",
                        "transitioned_by": "persona-risk-chief",
                    },
                ],
                "linked_experiments": ["exp-20260417-004"],
                "linked_artifacts": ["artifact-20260417-004-a"],
            },
        },
        "institutional_memory_entries": {
            "mem-7f2a1b9c-4d5e-4f6a-8b0c-9d1e2f3a4b5c": {
                "entry_id": "mem-7f2a1b9c-4d5e-4f6a-8b0c-9d1e2f3a4b5c",
                "knowledge_type": "incident_lesson",
                "content": {
                    "headline": "Risk exposure exceeded thresholds during BTC-regime shift due to stale parameter sync",
                    "body": (
                        "Detailed investigation into the incident on 2026-04-05 revealed that the latency in "
                        "parameter propagation from the Research Plane to the Execution Plane allowed the persona "
                        "'Alpha-Momentum' to maintain high exposure even as market volatility spiked. The lesson "
                        "learned is that parameter staleness signals must trigger an immediate safety halt or "
                        "aggressive de-risking if the drift exceeds 300ms during high-volatility regimes."
                    ),
                    "structured_payload": {
                        "affected_personas": ["Alpha-Momentum", "Beta-Arbitrage"],
                        "drift_observed_ms": 450,
                        "max_exposure_violation_pct": 12.5,
                    },
                    "tags": ["risk", "latency", "regime-shift", "btc"],
                },
                "source_event": {
                    "type": "postmortem_published",
                    "id": "pm-2026-04-05-btc-drift",
                    "href": "/operator/incidents/inc-2026-04-05-001/review",
                },
                "contributing_persona_ids": ["Alpha-Momentum"],
                "written_at": "2026-04-07T14:30:00Z",
                "write_authority": "incident-svc",
                "scope": {"type": "system_wide", "filter": None},
                "lifecycle": {"status": "active", "superseded_by": None},
                "usage": {"reuse_count": 28, "last_cited_at": "2026-04-19T08:45:00Z"},
            },
            "mem-2c3d4e5f-6a7b-8c9d-0e1f-2a3b4c5d6e7f": {
                "entry_id": "mem-2c3d4e5f-6a7b-8c9d-0e1f-2a3b4c5d6e7f",
                "knowledge_type": "regime_pattern",
                "content": {
                    "headline": "Momentum strategy underperforms in low-volatility sideways regimes",
                    "body": (
                        "Cross-regime replay analysis shows the momentum stack loses edge when volatility compresses "
                        "and breadth confirmation decays. Future deployments should widen confirmation thresholds "
                        "or reduce exposure scaling in sideways regimes."
                    ),
                    "structured_payload": {
                        "affected_strategy_family": "momentum",
                        "observed_regime": "low_vol_sideways",
                        "underperformance_window_days": 17,
                    },
                    "tags": ["regime", "momentum", "volatility"],
                },
                "source_event": {
                    "type": "evolution_decision_published",
                    "id": "evo-2026-04-10-regime-001",
                    "href": "/operator/mutation-review?decision=evo-2026-04-10-regime-001",
                },
                "contributing_persona_ids": ["persona-alpha", "p-macro-observer"],
                "written_at": "2026-04-10T09:15:00Z",
                "write_authority": "evolution-svc",
                "scope": {"type": "strategy_family", "filter": "momentum"},
                "lifecycle": {"status": "active", "superseded_by": None},
                "usage": {"reuse_count": 14, "last_cited_at": "2026-04-18T14:20:00Z"},
            },
            "mem-8e9f0a1b-2c3d-4e5f-6a7b-8c9d0e1f2a3b": {
                "entry_id": "mem-8e9f0a1b-2c3d-4e5f-6a7b-8c9d0e1f2a3b",
                "knowledge_type": "policy_precedent",
                "content": {
                    "headline": "Simultaneous drawdown across correlated instruments triggers portfolio-level halt",
                    "body": (
                        "This precedent was archived after newer capital-pool controls replaced the original halt "
                        "policy, but the older rationale remains relevant for historical incident interpretation."
                    ),
                    "structured_payload": {
                        "halt_threshold_pct": 9.0,
                        "correlation_bucket": "high_beta_cluster",
                    },
                    "tags": ["policy", "drawdown", "halt", "correlation"],
                },
                "source_event": {
                    "type": "incident_policy_archived",
                    "id": "policy-2026-03-28-portfolio-halt",
                    "href": "/operator/post-incident-review?incident=inc-2026-03-28-002",
                },
                "contributing_persona_ids": ["p-risk-analyst", "p-compliance-sponsor"],
                "written_at": "2026-03-28T16:00:00Z",
                "write_authority": "incident-svc",
                "scope": {"type": "system_wide", "filter": None},
                "lifecycle": {
                    "status": "superseded",
                    "superseded_by": "mem-7f2a1b9c-4d5e-4f6a-8b0c-9d1e2f3a4b5c",
                },
                "usage": {"reuse_count": 41, "last_cited_at": "2026-04-11T10:00:00Z"},
            },
        },
        "research_analyses": {
            "analysis-20260419-007-a": {
                "analysis_id": "analysis-20260419-007-a",
                "ticket_id": "rt-20260419-007",
                "experiment_id": "exp-20260419-012",
                "status": "completed",
                "run_at": "2026-04-19T20:40:00Z",
                "completed_at": "2026-04-19T20:43:00Z",
                "summary": {
                    "headline": "Volatility-cluster replay confirms faster momentum decay after the regime break.",
                    "narrative": (
                        "The shorter half-life configuration recovered more quickly after the March volatility "
                        "cluster while preserving enough signal selectivity to remain actionable."
                    ),
                    "verdict": "candidate_update",
                    "next_question": (
                        "Validate whether the shorter decay setting remains stable outside stress weeks before "
                        "promoting it into launch defaults."
                    ),
                },
                "metric_groups": [
                    {
                        "group_key": "performance",
                        "label": "Performance",
                        "description": "Return and efficiency metrics for the replay window.",
                        "metrics": [
                            {
                                "metric_key": "annualized_return",
                                "label": "Annualized return",
                                "value": 0.182,
                                "unit": "ratio",
                                "display_value": "18.2%",
                                "direction": "higher_is_better",
                                "baseline_value": 0.161,
                                "delta_value": 0.021,
                                "delta_display": "+2.1 pts",
                            },
                            {
                                "metric_key": "information_ratio",
                                "label": "Information ratio",
                                "value": 1.34,
                                "unit": "score",
                                "display_value": "1.34",
                                "direction": "higher_is_better",
                                "baseline_value": 1.19,
                                "delta_value": 0.15,
                                "delta_display": "+0.15",
                            },
                        ],
                    },
                    {
                        "group_key": "drawdown",
                        "label": "Drawdown",
                        "description": "Depth and recovery behavior during stressed windows.",
                        "metrics": [
                            {
                                "metric_key": "max_drawdown",
                                "label": "Max drawdown",
                                "value": 0.072,
                                "unit": "ratio",
                                "display_value": "7.2%",
                                "direction": "lower_is_better",
                                "baseline_value": 0.086,
                                "delta_value": -0.014,
                                "delta_display": "-1.4 pts",
                            },
                            {
                                "metric_key": "recovery_days",
                                "label": "Recovery days",
                                "value": 9,
                                "unit": "days",
                                "display_value": "9 days",
                                "direction": "lower_is_better",
                                "baseline_value": 12,
                                "delta_value": -3,
                                "delta_display": "-3 days",
                            },
                        ],
                    },
                    {
                        "group_key": "signal_quality",
                        "label": "Signal quality",
                        "description": "Stability and usefulness of the signal under the analyzed regime.",
                        "metrics": [
                            {
                                "metric_key": "signal_half_life_days",
                                "label": "Signal half-life",
                                "value": 3.8,
                                "unit": "days",
                                "display_value": "3.8 days",
                                "direction": "contextual",
                                "baseline_value": 5.2,
                                "delta_value": -1.4,
                                "delta_display": "-1.4 days",
                            },
                            {
                                "metric_key": "hit_rate",
                                "label": "Hit rate",
                                "value": 0.57,
                                "unit": "ratio",
                                "display_value": "57.0%",
                                "direction": "higher_is_better",
                                "baseline_value": 0.54,
                                "delta_value": 0.03,
                                "delta_display": "+3.0 pts",
                            },
                        ],
                    },
                ],
                "comparative_summary": {
                    "basis": "Most recent completed runs for the same ticket over the last 30 days.",
                    "baseline_analysis_id": "analysis-20260418-004-b",
                    "focus_metrics": [
                        "annualized_return",
                        "max_drawdown",
                        "signal_half_life_days",
                    ],
                    "comparisons": [
                        {
                            "analysis_id": "analysis-20260418-004-b",
                            "label": "Previous completed replay",
                            "status": "completed",
                            "run_at": "2026-04-18T16:15:00Z",
                            "delta_highlights": [
                                {
                                    "metric_key": "annualized_return",
                                    "change_label": "Higher return",
                                    "direction": "improved",
                                    "delta_display": "+2.1 pts",
                                    "interpretation": "Return improved while holding risk lower than the previous replay.",
                                },
                                {
                                    "metric_key": "max_drawdown",
                                    "change_label": "Lower drawdown",
                                    "direction": "improved",
                                    "delta_display": "-1.4 pts",
                                    "interpretation": "The updated decay setting reduced the worst loss depth in the stress window.",
                                },
                            ],
                        }
                    ],
                },
            },
            "analysis-20260418-004-b": {
                "analysis_id": "analysis-20260418-004-b",
                "ticket_id": "rt-20260419-007",
                "experiment_id": "exp-20260418-009",
                "status": "completed",
                "run_at": "2026-04-18T16:15:00Z",
                "completed_at": "2026-04-18T16:18:00Z",
                "summary": {
                    "headline": "Earlier replay showed decay pressure but with weaker drawdown improvement.",
                    "narrative": "The first replay suggested the regime-break hypothesis was real, but the improvement was not yet strong enough to justify a parameter change.",
                    "verdict": "observe",
                    "next_question": "Retest with a shorter decay half-life and compare recovery speed during the same volatility cluster.",
                },
                "metric_groups": [
                    {
                        "group_key": "performance",
                        "label": "Performance",
                        "description": "Return and efficiency metrics for the replay window.",
                        "metrics": [
                            {
                                "metric_key": "annualized_return",
                                "label": "Annualized return",
                                "value": 0.161,
                                "unit": "ratio",
                                "display_value": "16.1%",
                                "direction": "higher_is_better",
                                "baseline_value": None,
                                "delta_value": None,
                                "delta_display": None,
                            }
                        ],
                    },
                    {
                        "group_key": "drawdown",
                        "label": "Drawdown",
                        "description": "Depth and recovery behavior during stressed windows.",
                        "metrics": [
                            {
                                "metric_key": "max_drawdown",
                                "label": "Max drawdown",
                                "value": 0.086,
                                "unit": "ratio",
                                "display_value": "8.6%",
                                "direction": "lower_is_better",
                                "baseline_value": None,
                                "delta_value": None,
                                "delta_display": None,
                            }
                        ],
                    },
                    {
                        "group_key": "signal_quality",
                        "label": "Signal quality",
                        "description": "Stability and usefulness of the signal under the analyzed regime.",
                        "metrics": [
                            {
                                "metric_key": "signal_half_life_days",
                                "label": "Signal half-life",
                                "value": 5.2,
                                "unit": "days",
                                "display_value": "5.2 days",
                                "direction": "contextual",
                                "baseline_value": None,
                                "delta_value": None,
                                "delta_display": None,
                            }
                        ],
                    },
                ],
                "comparative_summary": {
                    "basis": "No earlier completed runs available for this ticket at the time of analysis.",
                    "baseline_analysis_id": "analysis-20260418-004-b",
                    "focus_metrics": [
                        "annualized_return",
                        "max_drawdown",
                        "signal_half_life_days",
                    ],
                    "comparisons": [],
                },
            },
            "analysis-20260417-001-c": {
                "analysis_id": "analysis-20260417-001-c",
                "ticket_id": "rt-20260415-001",
                "experiment_id": "exp-20260417-004",
                "status": "failed",
                "run_at": "2026-04-17T12:05:00Z",
                "completed_at": "2026-04-17T12:07:00Z",
                "summary": {
                    "headline": "Macro-event replay failed before final aggregation completed.",
                    "narrative": "The replay produced incomplete signal windows because one macro calendar partition was missing from the source snapshot.",
                    "verdict": "retry_required",
                    "next_question": "Repair the macro calendar partition and rerun aggregation before drawing conclusions.",
                },
                "metric_groups": [
                    {
                        "group_key": "pipeline_health",
                        "label": "Pipeline health",
                        "description": "Execution and aggregation completeness for the failed run.",
                        "metrics": [
                            {
                                "metric_key": "completed_windows_pct",
                                "label": "Completed windows",
                                "value": 0.62,
                                "unit": "ratio",
                                "display_value": "62.0%",
                                "direction": "higher_is_better",
                                "baseline_value": None,
                                "delta_value": None,
                                "delta_display": None,
                            }
                        ],
                    }
                ],
                "comparative_summary": {
                    "basis": "Comparison unavailable because the run did not complete.",
                    "baseline_analysis_id": "analysis-20260417-001-c",
                    "focus_metrics": [],
                    "comparisons": [],
                },
            },
        },
        "research_experiments": {
            "exp-20260419-012": {
                "experiment_id": "exp-20260419-012",
                "ticket_id": "rt-20260419-007",
                "experiment_name": "Momentum decay replay on March volatility cluster",
                "status": "completed",
                "queued_at": "2026-04-19T19:00:00Z",
                "started_at": "2026-04-19T19:03:00Z",
                "completed_at": "2026-04-19T20:15:00Z",
                "progress": {"percent": 100, "phase": "aggregation", "message": "Aggregation complete."},
                "strategy_selector": {"strategy_id": "strat-momentum-v4", "variant_id": "var-short-halflife"},
                "parameter_set": {"half_life_days": 5, "rebalance_window": "2d", "signal_threshold": 0.62},
                "run_config": {
                    "dataset_ref": "equities-us-2026Q1",
                    "time_range": {"start_at": "2026-03-01T00:00:00Z", "end_at": "2026-03-31T23:59:59Z"},
                    "execution_mode": "backtest",
                    "priority": "high",
                    "requested_by": "persona-risk-chief",
                },
                "launch_context": {"analysis_refs": ["analysis-20260418-004-b"]},
                "validation_warnings": [],
                "artifact_ids": ["artifact-20260418-005", "artifact-20260419-014"],
                "failure": {"reason_code": None, "message": None},
                "allowedActions": {"canCancel": False},
            },
            "exp-20260418-009": {
                "experiment_id": "exp-20260418-009",
                "ticket_id": "rt-20260419-007",
                "experiment_name": "Momentum decay baseline — short lookback variant",
                "status": "running",
                "queued_at": "2026-04-18T14:00:00Z",
                "started_at": "2026-04-18T14:05:00Z",
                "completed_at": None,
                "progress": {"percent": 62, "phase": "signal_aggregation", "message": "Aggregating signal windows…"},
                "strategy_selector": {"strategy_id": "strat-momentum-v4", "variant_id": "var-baseline"},
                "parameter_set": {"half_life_days": 10, "rebalance_window": "3d", "signal_threshold": 0.58},
                "run_config": {
                    "dataset_ref": "equities-us-2026Q1",
                    "time_range": {"start_at": "2026-02-01T00:00:00Z", "end_at": "2026-04-17T23:59:59Z"},
                    "execution_mode": "backtest",
                    "priority": "normal",
                    "requested_by": "persona-risk-chief",
                },
                "launch_context": {"analysis_refs": None},
                "validation_warnings": [
                    {"code": "WIDE_DATE_RANGE", "message": "Date range exceeds 60 days; runtime may be elevated."}
                ],
                "artifact_ids": [],
                "failure": {"reason_code": None, "message": None},
                "allowedActions": {"canCancel": True},
            },
            "exp-20260417-004": {
                "experiment_id": "exp-20260417-004",
                "ticket_id": "rt-20260415-001",
                "experiment_name": "Macro event signal quality — exclusion window test",
                "status": "failed",
                "queued_at": "2026-04-17T12:00:00Z",
                "started_at": "2026-04-17T12:02:00Z",
                "completed_at": "2026-04-17T12:07:00Z",
                "progress": {"percent": None, "phase": None, "message": None},
                "strategy_selector": {"strategy_id": "strat-macro-event-v2", "variant_id": None},
                "parameter_set": {"exclusion_window_hrs": 4, "signal_min_quality": 0.70},
                "run_config": {
                    "dataset_ref": "equities-us-2026Q1",
                    "time_range": {"start_at": "2026-01-01T00:00:00Z", "end_at": "2026-04-16T23:59:59Z"},
                    "execution_mode": "simulation",
                    "priority": "normal",
                    "requested_by": "persona-risk-chief",
                },
                "launch_context": {"analysis_refs": None},
                "validation_warnings": [],
                "artifact_ids": [],
                "failure": {
                    "reason_code": "MISSING_DATA_PARTITION",
                    "message": "Macro calendar partition for Q1 was missing from source snapshot.",
                },
                "allowedActions": {"canCancel": False},
            },
        },
        "research_search_documents": {
            "rt-20260419-007": {
                "result_id": "rt-20260419-007",
                "match_type": "ticket",
                "title": "Evaluate momentum factor decay in high-volatility regime",
                "excerpt": (
                    "Research ticket asks whether momentum factors lose predictive power during sustained "
                    "volatility spikes and whether current rebalancing windows are too slow."
                ),
                "linked_ticket_id": "rt-20260419-007",
                "linked_ticket_status": "in_progress",
                "relevance_score": 0.98,
                "updated_at": "2026-04-19T20:15:00Z",
                "search_text": (
                    "momentum decay volatility regime ticket predictive power rebalancing windows "
                    "sustained volatility spikes"
                ),
                "links": {
                    "result_detail": "/research/tickets/rt-20260419-007",
                    "linked_ticket_detail": "/research/tickets/rt-20260419-007",
                },
            },
            "exp-20260419-012": {
                "result_id": "exp-20260419-012",
                "match_type": "experiment",
                "title": "Momentum decay replay on March volatility cluster",
                "excerpt": (
                    "Experiment replay compares signal half-life before and after the March volatility "
                    "cluster and shows reduced persistence after regime break."
                ),
                "linked_ticket_id": "rt-20260419-007",
                "linked_ticket_status": "in_progress",
                "relevance_score": 0.91,
                "updated_at": "2026-04-19T20:14:30Z",
                "search_text": (
                    "momentum decay experiment replay march volatility cluster signal half life regime break"
                ),
                "links": {
                    "result_detail": "/research/experiments/exp-20260419-012",
                    "linked_ticket_detail": "/research/tickets/rt-20260419-007",
                },
            },
            "artifact-20260418-005": {
                "result_id": "artifact-20260418-005",
                "match_type": "artifact",
                "title": "Momentum regime-break feature set v5",
                "excerpt": (
                    "Artifact notes include volatility-regime bucketing and a shorter half-life decay "
                    "coefficient intended for stressed market windows."
                ),
                "linked_ticket_id": "rt-20260419-007",
                "linked_ticket_status": "in_progress",
                "relevance_score": 0.87,
                "updated_at": "2026-04-18T20:12:58Z",
                "search_text": (
                    "momentum artifact volatility regime bucketing shorter half life decay stressed market windows"
                ),
                "links": {
                    "result_detail": "/research/artifacts/artifact-20260418-005",
                    "linked_ticket_detail": "/research/tickets/rt-20260419-007",
                },
            },
            "rt-20260415-001": {
                "result_id": "rt-20260415-001",
                "match_type": "ticket",
                "title": "Validate signal quality on macro event windows",
                "excerpt": (
                    "Closed research ticket recorded weaker signal quality around scheduled macro events "
                    "and tested whether exclusion windows were sufficient."
                ),
                "linked_ticket_id": "rt-20260415-001",
                "linked_ticket_status": "closed",
                "relevance_score": 0.64,
                "updated_at": "2026-04-19T11:00:00Z",
                "search_text": (
                    "signal quality macro event windows exclusion windows scheduled macro events closed ticket"
                ),
                "links": {
                    "result_detail": "/research/tickets/rt-20260415-001",
                    "linked_ticket_detail": "/research/tickets/rt-20260415-001",
                },
            },
        },
        "research_search_index": {
            "rw02-search-index": {
                "adapter_id": "rw02-search-index",
                "snapshot_at": "2026-04-19T20:14:30Z",
                "adapter_state": "fresh",
                "indexed_match_types": ["ticket", "experiment", "artifact"],
                "source_watermarks": {
                    "tickets": "2026-04-19T20:14:10Z",
                    "experiments": "2026-04-19T20:13:42Z",
                    "artifacts": "2026-04-19T20:12:58Z",
                },
            }
        },
    }


class ReadSurfaceStore:
    _LOCAL_DATA_KEYS = {
        "deployment_plans": "deployment_plans",
        "approval_decisions": "approval_decisions",
        "capital_pools": "capital_pools",
        "persona_bindings": "bindings",
        "runtime_bindings": "runtime_bindings",
        "registry_entries": "registry_entries",
        "personas": "personas",
        "sessions": "sessions",
        "capability_snapshots": "capability_snapshots",
        "teaching_sessions": "teaching_sessions",
        "trainer_previews": "trainer_previews",
        "consultation_sessions": "consultation_sessions",
        "consult_policies": "consult_policies",
        "incidents": "incidents",
        "postmortems": "postmortems",
        "evolution_decisions": "evolution_decisions",
        "telemetry_summaries": "telemetry_summaries",
        "telemetry_performance": "telemetry_performance",
        "paper_live_drift_reports": "paper_live_drift_reports",
        "lineage_edges": "lineage_edges",
        "inspiration_graphs": "inspiration_graphs",
        "kill_switch": "kill_switch",
        "rollbacks": "rollbacks",
        "rollbacks_by_incident": "rollbacks_by_incident",
        "all_rollbacks": "all_rollbacks",
        "latest_runs": "latest_runs",
        "review_summaries": "review_summaries",
        "rollback_reviews": "rollback_reviews",
        "governance_audit_events": "governance_audit_events",
        "governance_review_queue_items": "governance_review_queue_items",
        "approval_queue_items": "approval_queue_items",
        "deployment_diffs": "deployment_diffs",
        "research_tickets": "research_tickets",
        "research_experiments": "research_experiments",
        "institutional_memory_entries": "institutional_memory_entries",
        "research_analyses": "research_analyses",
        "research_search_documents": "research_search_documents",
        "research_search_index": "research_search_index",
        "consult_requests": "consult_requests",
    }

    def __init__(
        self,
        storage_path: str,
        *,
        allow_local_snapshot_fallback: Optional[bool] = None,
    ) -> None:
        self._path = Path(storage_path)
        self._data: Dict[str, Any] = {}
        self._canonical = CanonicalSnapshotAdapter(snapshot_path=self._path)
        self._service = ServiceBackedReadAdapter(snapshot_path=self._path)
        if allow_local_snapshot_fallback is None:
            allow_local_snapshot_fallback = False
        self._allow_local_snapshot_fallback = allow_local_snapshot_fallback
        self._load_or_seed()

    def _load_or_seed(self) -> None:
        if self._path.exists():
            raw = self._path.read_text().strip()
            if raw:
                self._data = json.loads(raw)
                if self._allow_local_snapshot_fallback and self._backfill_local_contract_defaults():
                    self._save()
                return
        if self._allow_local_snapshot_fallback:
            self._data = _default_read_data()
            self._save()
            return
        self._data = {}

    def _backfill_local_contract_defaults(self) -> bool:
        changed = False
        default_data = _default_read_data()
        deployment_plans = self._data.get("deployment_plans")
        default_plans = default_data.get("deployment_plans", {})
        approval_decisions = self._data.get("approval_decisions")
        default_approval_decisions = default_data.get("approval_decisions", {})
        evolution_decisions = self._data.get("evolution_decisions")
        default_decisions = default_data.get("evolution_decisions", {})
        rollback_reviews = self._data.get("rollback_reviews")
        default_rollback_reviews = default_data.get("rollback_reviews", {})
        governance_review_queue_items = self._data.get("governance_review_queue_items")
        default_governance_review_queue_items = default_data.get(
            "governance_review_queue_items",
            {},
        )
        approval_queue_items = self._data.get("approval_queue_items")
        default_approval_queue_items = default_data.get("approval_queue_items", {})
        deployment_diffs = self._data.get("deployment_diffs")
        default_deployment_diffs = default_data.get("deployment_diffs", {})
        paper_live_drift_reports = self._data.get("paper_live_drift_reports")
        default_paper_live_drift_reports = default_data.get("paper_live_drift_reports", {})
        registry_entries = self._data.get("registry_entries")
        default_registry_entries = default_data.get("registry_entries", {})
        research_analyses = self._data.get("research_analyses")
        default_research_analyses = default_data.get("research_analyses", {})
        institutional_memory_entries = self._data.get("institutional_memory_entries")
        default_institutional_memory_entries = default_data.get("institutional_memory_entries", {})
        research_search_documents = self._data.get("research_search_documents")
        default_research_search_documents = default_data.get("research_search_documents", {})
        research_search_index = self._data.get("research_search_index")
        default_research_search_index = default_data.get("research_search_index", {})
        inspiration_graphs = self._data.get("inspiration_graphs")
        default_inspiration_graphs = default_data.get("inspiration_graphs", {})
        trainer_previews = self._data.get("trainer_previews")
        default_trainer_previews = default_data.get("trainer_previews", {})

        if isinstance(deployment_plans, dict):
            for plan_id, default_plan in default_plans.items():
                existing_plan = deployment_plans.get(plan_id)
                if not isinstance(existing_plan, dict):
                    continue
                if "plan_id" not in existing_plan and default_plan.get("plan_id") is not None:
                    existing_plan["plan_id"] = default_plan["plan_id"]
                    changed = True

        if approval_decisions is None:
            self._data["approval_decisions"] = json.loads(json.dumps(default_approval_decisions))
            changed = True
        elif isinstance(approval_decisions, dict):
            for decision_id, default_decision in default_approval_decisions.items():
                if decision_id not in approval_decisions:
                    approval_decisions[decision_id] = json.loads(json.dumps(default_decision))
                    changed = True
                    continue
                existing_decision = approval_decisions.get(decision_id)
                if not isinstance(existing_decision, dict):
                    continue
                for key, value in default_decision.items():
                    if key not in existing_decision and value is not None:
                        existing_decision[key] = json.loads(json.dumps(value))
                        changed = True

        if evolution_decisions is None:
            self._data["evolution_decisions"] = json.loads(json.dumps(default_decisions))
            changed = True
        elif isinstance(evolution_decisions, dict):
            for decision_id, default_decision in default_decisions.items():
                if decision_id not in evolution_decisions:
                    evolution_decisions[decision_id] = json.loads(json.dumps(default_decision))
                    changed = True
                    continue
                existing_decision = evolution_decisions.get(decision_id)
                if not isinstance(existing_decision, dict):
                    continue
                for key, value in default_decision.items():
                    if key not in existing_decision and value is not None:
                        existing_decision[key] = json.loads(json.dumps(value))
                        changed = True

        if rollback_reviews is None:
            self._data["rollback_reviews"] = json.loads(json.dumps(default_rollback_reviews))
            changed = True
        elif isinstance(rollback_reviews, dict):
            for rollback_id, default_review in default_rollback_reviews.items():
                if rollback_id not in rollback_reviews:
                    rollback_reviews[rollback_id] = json.loads(json.dumps(default_review))
                    changed = True

        if governance_review_queue_items is None:
            self._data["governance_review_queue_items"] = json.loads(
                json.dumps(default_governance_review_queue_items)
            )
            changed = True
        elif isinstance(governance_review_queue_items, dict):
            for item_id, default_item in default_governance_review_queue_items.items():
                if item_id not in governance_review_queue_items:
                    governance_review_queue_items[item_id] = json.loads(
                        json.dumps(default_item)
                    )
                    changed = True

        if approval_queue_items is None:
            self._data["approval_queue_items"] = json.loads(
                json.dumps(default_approval_queue_items)
            )
            changed = True
        elif isinstance(approval_queue_items, dict):
            for item_id, default_item in default_approval_queue_items.items():
                if item_id not in approval_queue_items:
                    approval_queue_items[item_id] = json.loads(json.dumps(default_item))
                    changed = True

        if deployment_diffs is None:
            self._data["deployment_diffs"] = json.loads(json.dumps(default_deployment_diffs))
            changed = True
        elif isinstance(deployment_diffs, dict):
            for plan_id, default_diff in default_deployment_diffs.items():
                if plan_id not in deployment_diffs:
                    deployment_diffs[plan_id] = json.loads(json.dumps(default_diff))
                    changed = True

        if paper_live_drift_reports is None:
            self._data["paper_live_drift_reports"] = json.loads(
                json.dumps(default_paper_live_drift_reports)
            )
            changed = True
        elif isinstance(paper_live_drift_reports, dict):
            for runtime_id, default_report in default_paper_live_drift_reports.items():
                if runtime_id not in paper_live_drift_reports:
                    paper_live_drift_reports[runtime_id] = json.loads(
                        json.dumps(default_report)
                    )
                    changed = True

        if registry_entries is None:
            self._data["registry_entries"] = json.loads(json.dumps(default_registry_entries))
            changed = True
        elif isinstance(registry_entries, dict):
            for artifact_id, default_entry in default_registry_entries.items():
                if artifact_id not in registry_entries:
                    registry_entries[artifact_id] = json.loads(json.dumps(default_entry))
                    changed = True

        if research_analyses is None:
            self._data["research_analyses"] = json.loads(json.dumps(default_research_analyses))
            changed = True
        elif isinstance(research_analyses, dict):
            for analysis_id, default_analysis in default_research_analyses.items():
                if analysis_id not in research_analyses:
                    research_analyses[analysis_id] = json.loads(json.dumps(default_analysis))
                    changed = True

        if institutional_memory_entries is None:
            self._data["institutional_memory_entries"] = json.loads(
                json.dumps(default_institutional_memory_entries)
            )
            changed = True
        elif isinstance(institutional_memory_entries, dict):
            for entry_id, default_entry in default_institutional_memory_entries.items():
                if entry_id not in institutional_memory_entries:
                    institutional_memory_entries[entry_id] = json.loads(json.dumps(default_entry))
                    changed = True

        if research_search_documents is None:
            self._data["research_search_documents"] = json.loads(
                json.dumps(default_research_search_documents)
            )
            changed = True
        elif isinstance(research_search_documents, dict):
            for result_id, default_document in default_research_search_documents.items():
                if result_id not in research_search_documents:
                    research_search_documents[result_id] = json.loads(json.dumps(default_document))
                    changed = True

        if research_search_index is None:
            self._data["research_search_index"] = json.loads(json.dumps(default_research_search_index))
            changed = True
        elif isinstance(research_search_index, dict):
            for adapter_id, default_index in default_research_search_index.items():
                if adapter_id not in research_search_index:
                    research_search_index[adapter_id] = json.loads(json.dumps(default_index))
                    changed = True

        if inspiration_graphs is None:
            self._data["inspiration_graphs"] = json.loads(json.dumps(default_inspiration_graphs))
            changed = True
        elif isinstance(inspiration_graphs, dict):
            for artifact_id, default_graph in default_inspiration_graphs.items():
                if artifact_id not in inspiration_graphs:
                    inspiration_graphs[artifact_id] = json.loads(json.dumps(default_graph))
                    changed = True

        if trainer_previews is None:
            self._data["trainer_previews"] = json.loads(json.dumps(default_trainer_previews))
            changed = True
        elif isinstance(trainer_previews, dict):
            for session_id, default_preview in default_trainer_previews.items():
                if session_id not in trainer_previews:
                    trainer_previews[session_id] = json.loads(json.dumps(default_preview))
                    changed = True

        return changed

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._data, indent=2, ensure_ascii=True))

    def _local_dataset(self, dataset: str) -> Any:
        key = self._LOCAL_DATA_KEYS.get(dataset, dataset)
        return self._data.get(key)

    def _local_fallback(self, dataset: str) -> Any:
        if not self._allow_local_snapshot_fallback:
            return None
        return self._local_dataset(dataset)

    def dataset_source(self, dataset: str) -> str:
        if dataset in CanonicalSnapshotAdapter._DATASETS:
            available, _ = self._canonical.list_records(dataset)
            if available:
                return "canonical"
        if dataset in ServiceBackedReadAdapter._DATASETS:
            available, _ = self._service.list_records(dataset)
            if available:
                if (
                    self._allow_local_snapshot_fallback
                    and self._service._resolve_path(dataset) is None
                ):
                    return "local_snapshot"
                return "service_store"
        local_payload = self._local_fallback(dataset)
        if local_payload not in (None, "", [], {}):
            return "local_snapshot"
        return "missing"

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
            "validity": raw.get("validity"),
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

    @staticmethod
    def _project_service_persona(raw: Dict[str, Any]) -> Dict[str, Any]:
        persona_id = raw.get("persona_id") or raw.get("id")
        return {
            "id": persona_id,
            "persona_id": persona_id,
            "name": raw.get("name"),
            "mandate": raw.get("mandate"),
            "lifecycle_state": raw.get("lifecycle_state"),
            "created_at": raw.get("created_at"),
            "strategy_family": raw.get("strategy_family"),
            "status": raw.get("status"),
            "updated_at": raw.get("updated_at"),
            "metadata": raw.get("metadata", {}),
        }

    @staticmethod
    def _project_service_session(raw: Dict[str, Any]) -> Dict[str, Any]:
        session_id = raw.get("session_id") or raw.get("id")
        return {
            "id": session_id,
            "session_id": session_id,
            "persona_id": raw.get("persona_id"),
            "session_type": raw.get("session_type"),
            "status": raw.get("status"),
            "started_at": raw.get("started_at"),
            "ended_at": raw.get("ended_at"),
            "capability_snapshot_id": raw.get("capability_snapshot_id"),
            "trace_id": raw.get("trace_id"),
            "request_id": raw.get("request_id"),
            "runtime_binding_id": raw.get("runtime_binding_id"),
            "deployment_stage": raw.get("deployment_stage"),
            "capital_pool_id": raw.get("capital_pool_id"),
            "context_bundle_ref": raw.get("context_bundle_ref"),
            "metadata": raw.get("metadata", {}),
        }

    @staticmethod
    def _project_service_evolution_decision(raw: Dict[str, Any]) -> Dict[str, Any]:
        decision_id = raw.get("decision_id") or raw.get("id")
        decision_state = raw.get("decision_state") or raw.get("status")
        linked_incident_id = raw.get("linked_incident_id") or raw.get("incident_ref")
        target_id = raw.get("target_id") or raw.get("artifact_id")
        return {
            "id": decision_id,
            "decision_id": decision_id,
            "action_type": raw.get("action_type"),
            "risk_level": raw.get("risk_level"),
            "status": decision_state,
            "decision_state": decision_state,
            "incident_ref": linked_incident_id,
            "linked_incident_id": linked_incident_id,
            "linked_postmortem_id": raw.get("linked_postmortem_id"),
            "artifact_id": target_id,
            "target_type": raw.get("target_type"),
            "target_id": target_id,
            "target_version": raw.get("target_version"),
            "target_stage": raw.get("target_stage"),
            "approval_decision_id": raw.get("approval_decision_id"),
            "created_at": raw.get("created_at"),
            "updated_at": raw.get("updated_at"),
            "notes": raw.get("notes"),
            "rationale": raw.get("rationale"),
            "created_by_role": raw.get("created_by_role"),
            "created_by_id": raw.get("created_by_id"),
            "evidence_refs": raw.get("evidence_refs") or [],
            "threshold_snapshots": raw.get("threshold_snapshots") or [],
            "review_chain": raw.get("review_chain") or [],
            "proposed_changes": raw.get("proposed_changes"),
            "risk_assessment": raw.get("risk_assessment"),
            "required_approvals": raw.get("required_approvals"),
            "rollback_followthrough": raw.get("rollback_followthrough"),
            "metadata": raw.get("metadata"),
            "execution_result": raw.get("execution_result"),
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
        available, raw_personas = self._service.list_records("personas")
        if available:
            personas = [self._project_service_persona(persona) for persona in raw_personas]
        else:
            personas = list((self._local_fallback("personas") or {}).values())
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
            pools = list((self._local_fallback("capital_pools") or {}).values())
        if status:
            pools = [p for p in pools if p.get("status") == status]
        if risk_policy_ref:
            pools = [p for p in pools if p.get("risk_policy_ref") == risk_policy_ref]
        return sorted(pools, key=lambda x: x.get("id", ""))

    def list_bindings(
        self,
        persona_id: Optional[str] = None,
        capital_pool_id: Optional[str] = None,
        role: Optional[str] = None,
        validity: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        available, raw_bindings = self._canonical.list_records("persona_bindings")
        if available:
            bindings = [self._project_canonical_binding(binding) for binding in raw_bindings]
        else:
            bindings = list((self._local_fallback("persona_bindings") or {}).values())
        if persona_id:
            bindings = [b for b in bindings if b.get("persona_id") == persona_id]
        if capital_pool_id:
            bindings = [b for b in bindings if b.get("capital_pool_id") == capital_pool_id]
        if role:
            bindings = [b for b in bindings if b.get("role") == role]
        if validity:
            bindings = [b for b in bindings if b.get("validity") == validity]
        return sorted(bindings, key=lambda x: x.get("id", ""))

    def list_deployment_plans(
        self,
        status: Optional[str] = None,
        capital_pool_id: Optional[str] = None,
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
            plans = list((self._local_fallback("deployment_plans") or {}).values())
        if status:
            plans = [
                p for p in plans
                if str(p.get("status") or "").lower() == status.lower()
            ]
        if capital_pool_id:
            plans = [
                p for p in plans
                if str(p.get("capital_pool_id") or p.get("target_pool_id") or "") == capital_pool_id
            ]
        return sorted(plans, key=lambda x: x.get("id", ""))

    def list_approval_decisions(
        self,
        outcome: Optional[str] = None,
        state: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        available, raw_decisions = self._canonical.list_records("approval_decisions")
        if available:
            decisions = [self._project_canonical_approval_decision(decision) for decision in raw_decisions]
        else:
            decisions = list((self._local_fallback("approval_decisions") or {}).values())
        if outcome:
            decisions = [d for d in decisions if str(d.get("outcome") or "").lower() == outcome.lower()]
        if state:
            decisions = [
                d for d in decisions
                if str(d.get("state") or "").lower() == state.lower()
            ]
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
            bindings = list((self._local_fallback("runtime_bindings") or {}).values())
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

    def list_registry_entries(self) -> List[Dict[str, Any]]:
        available, raw_entries = self._canonical.list_records("registry_entries")
        if available and raw_entries:
            return list(raw_entries)
        return list((self._local_fallback("registry_entries") or {}).values())

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
        return (self._local_fallback("deployment_plans") or {}).get(plan_id)

    def get_approval_decision(self, decision_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not decision_id:
            return None
        available, raw = self._canonical.approval_decision(decision_id)
        if available:
            return self._project_canonical_approval_decision(raw) if raw else None
        return (self._local_fallback("approval_decisions") or {}).get(decision_id)

    def get_capital_pool(self, pool_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not pool_id:
            return None
        available, raw = self._canonical.capital_pool(pool_id)
        if available:
            return self._project_canonical_capital_pool(raw) if raw else None
        return (self._local_fallback("capital_pools") or {}).get(pool_id)

    def get_binding(self, binding_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not binding_id:
            return None
        available, raw = self._canonical.binding(binding_id)
        if available:
            return self._project_canonical_binding(raw) if raw else None
        return (self._local_fallback("persona_bindings") or {}).get(binding_id)

    def get_bindings_for_pool(self, pool_id: Optional[str]) -> List[Dict[str, Any]]:
        if not pool_id:
            return []
        available, bindings = self._canonical.bindings_for_pool(pool_id)
        if available:
            return [self._project_canonical_binding(binding) for binding in bindings]
        return [
            binding
            for binding in (self._local_fallback("persona_bindings") or {}).values()
            if binding.get("capital_pool_id") == pool_id
        ]

    def get_bindings_for_persona(self, persona_id: Optional[str]) -> Optional[List[Dict[str, Any]]]:
        """Return all bindings where the given persona_id is the owner.

        Returns None when the persona itself cannot be verified (degraded mode).
        """
        if not persona_id:
            return None
        if self.get_persona(persona_id) is None:
            return None
        available, raw_bindings = self._canonical.list_records("persona_bindings")
        if available:
            return [
                self._project_canonical_binding(binding)
                for binding in raw_bindings
                if binding.get("persona_id") == persona_id
            ]
        return [
            binding
            for binding in (self._local_fallback("persona_bindings") or {}).values()
            if binding.get("persona_id") == persona_id
        ]

    def get_persona(self, persona_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not persona_id:
            return None
        available, raw = self._service.record("personas", persona_id)
        if available:
            return self._project_service_persona(raw) if raw else None
        return (self._local_fallback("personas") or {}).get(persona_id)

    def get_runtime_binding(self, binding_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not binding_id:
            return None
        available, raw = self._canonical.runtime_binding(binding_id)
        if available:
            return self._project_canonical_runtime_binding(raw) if raw else None
        return (self._local_fallback("runtime_bindings") or {}).get(binding_id)

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
        for binding in (self._local_fallback("runtime_bindings") or {}).values():
            if str(binding.get("runtime_id") or binding.get("id") or "") == runtime_id:
                return binding
        return None

    def get_rollbacks(self, runtime_id: Optional[str]) -> List[Dict[str, Any]]:
        if not runtime_id:
            return []
        return list((self._local_fallback("rollbacks") or {}).get(runtime_id, []))

    def get_allowed_actions(self, plan_id: str) -> Dict[str, Any]:
        plan = self.get_deployment_plan(plan_id)
        decision = self.get_approval_decision(plan.get("approval_decision_id")) if plan else None
        if plan and (plan.get("status") is not None or plan.get("target_stage") is not None):
            return {
                "canPromoteToPaper": self._derive_can_promote_to_paper(plan, decision)
            }
        if self._allow_local_snapshot_fallback:
            return (self._local_fallback("allowed_actions") or {}).get(
                plan_id,
                {"canPromoteToPaper": False},
            )
        return {"canPromoteToPaper": False}

    def get_latest_run(self, plan_id: str) -> Dict[str, Any]:
        if self._allow_local_snapshot_fallback:
            return (self._local_fallback("latest_runs") or {}).get(plan_id, {"progress": 0.0})
        return None

    def get_review_summary(self, plan_id: str) -> Dict[str, Any]:
        summary = dict((self._local_fallback("review_summaries") or {}).get(plan_id, {}))
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
        if not summary:
            return None
        if "riskSummary" not in summary or not summary["riskSummary"]:
            summary["riskSummary"] = "Risk summary unavailable."
        return summary

    def list_governance_review_queue_items(
        self,
        *,
        item_types: Optional[List[str]] = None,
        risk_levels: Optional[List[str]] = None,
        statuses: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        items = list((self._local_fallback("governance_review_queue_items") or {}).values())

        if item_types:
            requested_item_types = {value for value in item_types if value}
            items = [
                item
                for item in items
                if str(item.get("item_type") or "") in requested_item_types
            ]
        if risk_levels:
            requested_risk_levels = {value for value in risk_levels if value}
            items = [
                item
                for item in items
                if str(item.get("risk_level") or "") in requested_risk_levels
            ]
        if statuses:
            requested_statuses = {value for value in statuses if value}
            items = [
                item
                for item in items
                if str(item.get("status") or "") in requested_statuses
            ]

        projected_items: List[Dict[str, Any]] = []
        for item in items:
            projected_items.append(
                {
                    "item_id": item.get("item_id"),
                    "item_type": item.get("item_type"),
                    "risk_level": item.get("risk_level"),
                    "submitted_at": item.get("submitted_at"),
                    "submitted_by": item.get("submitted_by"),
                    "governance_outcome": item.get("governance_outcome"),
                    "allowedActions": json.loads(json.dumps(item.get("allowedActions") or {})),
                    "review_summary": json.loads(json.dumps(item.get("review_summary") or {})),
                }
            )

        return projected_items

    def list_approval_queue_items(
        self,
        *,
        decision_types: Optional[List[str]] = None,
        risk_levels: Optional[List[str]] = None,
        decision_states: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        items = list((self._local_fallback("approval_queue_items") or {}).values())

        if decision_types:
            requested_types = {value for value in decision_types if value}
            items = [
                item
                for item in items
                if str(item.get("decision_type") or "") in requested_types
            ]
        if risk_levels:
            requested_risk_levels = {value for value in risk_levels if value}
            items = [
                item
                for item in items
                if str(item.get("risk_level") or "") in requested_risk_levels
            ]
        if decision_states:
            requested_states = {value for value in decision_states if value}
            items = [
                item
                for item in items
                if str(item.get("decision_state") or "") in requested_states
            ]

        projected_items: List[Dict[str, Any]] = []
        for item in items:
            projected_items.append(
                {
                    "decision_id": item.get("decision_id"),
                    "decision_type": item.get("decision_type"),
                    "risk_level": item.get("risk_level"),
                    "submitted_at": item.get("submitted_at"),
                    "submitted_by": item.get("submitted_by"),
                    "decision_state": item.get("decision_state"),
                    "allowedActions": json.loads(json.dumps(item.get("allowedActions") or {})),
                    "decision_context": json.loads(json.dumps(item.get("decision_context") or {})),
                }
            )

        return projected_items

    def get_deployment_diff(self, plan_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not plan_id:
            return None
        diff = (self._local_fallback("deployment_diffs") or {}).get(plan_id)
        return json.loads(json.dumps(diff)) if diff else None

    # ------------------------------------------------------------------ #
    # Research Ticket surfaces (RW-01)
    # ------------------------------------------------------------------ #

    @staticmethod
    def _research_ticket_allowed_actions(status: Optional[str]) -> Dict[str, bool]:
        normalized = str(status or "").strip().lower()
        if normalized == "archived":
            return {
                "canEdit": False,
                "canClose": False,
                "canArchive": False,
            }
        if normalized == "closed":
            return {
                "canEdit": False,
                "canClose": False,
                "canArchive": True,
            }
        if normalized in {"open", "in_progress"}:
            return {
                "canEdit": True,
                "canClose": True,
                "canArchive": False,
            }
        return {
            "canEdit": False,
            "canClose": False,
            "canArchive": False,
        }

    @classmethod
    def _project_research_ticket_summary(cls, ticket: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "ticket_id": ticket.get("ticket_id"),
            "title": ticket.get("title"),
            "status": ticket.get("status"),
            "priority": ticket.get("priority"),
            "owner": ticket.get("owner"),
            "created_at": ticket.get("created_at"),
            "updated_at": ticket.get("updated_at"),
            "allowedActions": cls._research_ticket_allowed_actions(ticket.get("status")),
        }

    @classmethod
    def _project_research_ticket_detail(cls, ticket: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "ticket_id": ticket.get("ticket_id"),
            "title": ticket.get("title"),
            "description": ticket.get("description"),
            "status": ticket.get("status"),
            "priority": ticket.get("priority"),
            "owner": ticket.get("owner"),
            "created_at": ticket.get("created_at"),
            "updated_at": ticket.get("updated_at"),
            "closed_at": ticket.get("closed_at"),
            "archived_at": ticket.get("archived_at"),
            "lifecycle_history": json.loads(json.dumps(ticket.get("lifecycle_history") or [])),
            "linked_experiments": list(ticket.get("linked_experiments") or []),
            "linked_artifacts": list(ticket.get("linked_artifacts") or []),
            "allowedActions": cls._research_ticket_allowed_actions(ticket.get("status")),
        }

    def list_research_tickets(
        self,
        *,
        statuses: Optional[List[str]] = None,
        owner: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        tickets = self._read_dataset_records("research_tickets")
        if statuses:
            requested_statuses = {str(value).strip().lower() for value in statuses if str(value).strip()}
            tickets = [
                ticket
                for ticket in tickets
                if str(ticket.get("status") or "").strip().lower() in requested_statuses
            ]
        if owner:
            requested_owner = str(owner).strip()
            tickets = [
                ticket
                for ticket in tickets
                if str(ticket.get("owner") or "").strip() == requested_owner
            ]

        tickets.sort(
            key=lambda ticket: (
                _parse_rfc3339(ticket.get("updated_at")) or _parse_rfc3339(ticket.get("created_at")) or datetime.min
            ),
            reverse=True,
        )
        return [self._project_research_ticket_summary(ticket) for ticket in tickets]

    def get_research_ticket(self, ticket_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not ticket_id:
            return None
        available, ticket = self._service.record("research_tickets", ticket_id)
        if not available:
            ticket = (self._local_fallback("research_tickets") or {}).get(ticket_id)
        if not ticket:
            return None
        return self._project_research_ticket_detail(ticket)

    def create_research_ticket(
        self,
        *,
        title: str,
        description: str,
        priority: str,
        owner: str,
        actor_id: str,
        created_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        service_store_path = self._service._resolve_path("research_tickets")
        persist_service_store = service_store_path is not None
        tickets: Optional[Dict[str, Any]]
        if persist_service_store:
            available, service_tickets = self._service.list_records("research_tickets")
            if not available and service_store_path.exists():
                raise RuntimeError("Research ticket store is unavailable.")
            tickets = {
                str(ticket.get("ticket_id") or ticket.get("id") or ""): json.loads(json.dumps(ticket))
                for ticket in service_tickets
                if isinstance(ticket, dict) and str(ticket.get("ticket_id") or ticket.get("id") or "").strip()
            }
        else:
            tickets = self._local_fallback("research_tickets")
        if tickets is None:
            raise RuntimeError("Research ticket store is unavailable.")

        timestamp = created_at or _utc_now_rfc3339()
        ticket_id = f"rt-{timestamp[:10].replace('-', '')}-{len(tickets) + 1:03d}"
        while ticket_id in tickets:
            ticket_id = f"rt-{timestamp[:10].replace('-', '')}-{len(tickets) + 2:03d}"

        ticket = {
            "ticket_id": ticket_id,
            "title": title,
            "description": description,
            "status": "open",
            "priority": priority,
            "owner": owner,
            "created_at": timestamp,
            "updated_at": timestamp,
            "closed_at": None,
            "archived_at": None,
            "lifecycle_history": [
                {
                    "from_status": None,
                    "to_status": "open",
                    "transitioned_at": timestamp,
                    "transitioned_by": actor_id,
                }
            ],
            "linked_experiments": [],
            "linked_artifacts": [],
        }
        tickets[ticket_id] = ticket
        if persist_service_store:
            self._service.write_records("research_tickets", tickets)
        else:
            self._save()
        return self._project_research_ticket_detail(ticket)

    def patch_research_ticket(
        self,
        ticket_id: str,
        *,
        patch: Dict[str, Any],
        actor_id: str,
        updated_at: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        service_store_path = self._service._resolve_path("research_tickets")
        persist_service_store = service_store_path is not None
        tickets: Optional[Dict[str, Any]]
        if persist_service_store:
            available, service_tickets = self._service.list_records("research_tickets")
            if not available and service_store_path.exists():
                return None
            tickets = {
                str(ticket.get("ticket_id") or ticket.get("id") or ""): json.loads(json.dumps(ticket))
                for ticket in service_tickets
                if isinstance(ticket, dict) and str(ticket.get("ticket_id") or ticket.get("id") or "").strip()
            }
        else:
            tickets = self._local_fallback("research_tickets")
        if tickets is None:
            return None
        ticket = tickets.get(ticket_id)
        if ticket is None:
            return None

        timestamp = updated_at or _utc_now_rfc3339()
        editable_fields = {"title", "description", "priority", "owner"}
        for field in editable_fields:
            if field in patch:
                ticket[field] = patch[field]

        next_status = patch.get("status")
        if next_status is not None and next_status != ticket.get("status"):
            previous_status = ticket.get("status")
            ticket["status"] = next_status
            if next_status == "closed":
                ticket["closed_at"] = timestamp
                ticket["archived_at"] = None
            elif next_status == "archived":
                ticket["archived_at"] = timestamp
                if ticket.get("closed_at") is None:
                    ticket["closed_at"] = timestamp
            else:
                if next_status in {"open", "in_progress"}:
                    ticket["closed_at"] = None
                if next_status != "archived":
                    ticket["archived_at"] = None
            ticket.setdefault("lifecycle_history", []).append(
                {
                    "from_status": previous_status,
                    "to_status": next_status,
                    "transitioned_at": timestamp,
                    "transitioned_by": actor_id,
                }
            )

        ticket["updated_at"] = timestamp
        if persist_service_store:
            self._service.write_records("research_tickets", tickets)
        else:
            self._save()
        return self._project_research_ticket_detail(ticket)

    @staticmethod
    def _project_institutional_memory_summary(entry: Dict[str, Any]) -> Dict[str, Any]:
        entry_id = str(entry.get("entry_id") or entry.get("id") or "")
        content = entry.get("content") if isinstance(entry.get("content"), dict) else {}
        scope = entry.get("scope") if isinstance(entry.get("scope"), dict) else {}
        lifecycle = entry.get("lifecycle") if isinstance(entry.get("lifecycle"), dict) else {}
        usage = entry.get("usage") if isinstance(entry.get("usage"), dict) else {}
        return {
            "entry_id": entry_id,
            "knowledge_type": entry.get("knowledge_type"),
            "headline": content.get("headline"),
            "scope": scope.get("type"),
            "scope_filter": scope.get("filter"),
            "written_at": entry.get("written_at"),
            "write_authority": entry.get("write_authority"),
            "tags": list(content.get("tags") or []),
            "reuse_count": usage.get("reuse_count") or 0,
            "is_superseded": str(lifecycle.get("status") or "").strip().lower() == "superseded",
            "route_href": f"/knowledge/memory/{entry_id}",
        }

    @staticmethod
    def _project_institutional_memory_detail(entry: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "entry_id": entry.get("entry_id") or entry.get("id"),
            "knowledge_type": entry.get("knowledge_type"),
            "content": json.loads(json.dumps(entry.get("content") or {})),
            "source_event": json.loads(json.dumps(entry.get("source_event") or {})),
            "contributing_persona_ids": list(entry.get("contributing_persona_ids") or []),
            "written_at": entry.get("written_at"),
            "write_authority": entry.get("write_authority"),
            "scope": json.loads(json.dumps(entry.get("scope") or {})),
            "lifecycle": json.loads(json.dumps(entry.get("lifecycle") or {})),
            "usage": json.loads(json.dumps(entry.get("usage") or {})),
        }

    def list_institutional_memory_entries(self) -> List[Dict[str, Any]]:
        entries = self._read_dataset_records("institutional_memory_entries")
        entries.sort(
            key=lambda entry: (
                _parse_rfc3339(entry.get("written_at")) or datetime.min,
                int(((entry.get("usage") or {}).get("reuse_count") or 0)),
            ),
            reverse=True,
        )
        return [self._project_institutional_memory_summary(entry) for entry in entries]

    def get_institutional_memory_entry(self, entry_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not entry_id:
            return None
        available, entry = self._service.record("institutional_memory_entries", entry_id)
        if not available:
            entry = (self._local_fallback("institutional_memory_entries") or {}).get(entry_id)
        if not entry:
            return None
        return self._project_institutional_memory_detail(entry)

    # ------------------------------------------------------------------ #
    # Research Analyze surfaces (RW-03)
    # ------------------------------------------------------------------ #

    @staticmethod
    def _project_research_analysis_summary(analysis: Dict[str, Any]) -> Dict[str, Any]:
        metric_groups = list(analysis.get("metric_groups") or [])
        return {
            "analysis_id": analysis.get("analysis_id"),
            "ticket_id": analysis.get("ticket_id"),
            "experiment_id": analysis.get("experiment_id"),
            "status": analysis.get("status"),
            "run_at": analysis.get("run_at"),
            "summary": {
                "headline": ((analysis.get("summary") or {}).get("headline")),
                "verdict": ((analysis.get("summary") or {}).get("verdict")),
            },
            "metric_group_refs": [
                str(group.get("group_key") or "")
                for group in metric_groups
                if str(group.get("group_key") or "").strip()
            ],
        }

    @staticmethod
    def _project_research_analysis_detail(analysis: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "analysis_id": analysis.get("analysis_id"),
            "ticket_id": analysis.get("ticket_id"),
            "experiment_id": analysis.get("experiment_id"),
            "status": analysis.get("status"),
            "run_at": analysis.get("run_at"),
            "completed_at": analysis.get("completed_at"),
            "summary": json.loads(json.dumps(analysis.get("summary") or {})),
            "metric_groups": json.loads(json.dumps(analysis.get("metric_groups") or [])),
            "comparative_summary": json.loads(json.dumps(analysis.get("comparative_summary") or {})),
        }

    @staticmethod
    def _date_range_cutoff_token(date_range: Optional[str]) -> Optional[int]:
        if date_range == "24h":
            return 1
        if date_range == "7d":
            return 7
        if date_range == "30d":
            return 30
        if date_range == "90d":
            return 90
        return None

    def list_research_analyses(
        self,
        *,
        ticket_id: Optional[str] = None,
        experiment_id: Optional[str] = None,
        statuses: Optional[List[str]] = None,
        date_range: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        analyses = list((self._local_fallback("research_analyses") or {}).values())
        if ticket_id:
            analyses = [
                analysis
                for analysis in analyses
                if str(analysis.get("ticket_id") or "") == str(ticket_id)
            ]
        if experiment_id:
            analyses = [
                analysis
                for analysis in analyses
                if str(analysis.get("experiment_id") or "") == str(experiment_id)
            ]
        if statuses:
            requested_statuses = {str(value).strip().lower() for value in statuses if str(value).strip()}
            analyses = [
                analysis
                for analysis in analyses
                if str(analysis.get("status") or "").strip().lower() in requested_statuses
            ]
        cutoff_days = self._date_range_cutoff_token(date_range)
        if cutoff_days is not None:
            reference_now = datetime.utcnow()
            analyses = [
                analysis
                for analysis in analyses
                if (
                    (parsed := _parse_rfc3339(analysis.get("run_at"))) is not None
                    and (reference_now - parsed.replace(tzinfo=None)).days < cutoff_days
                )
            ]

        analyses.sort(
            key=lambda analysis: _parse_rfc3339(analysis.get("run_at")) or datetime.min,
            reverse=True,
        )
        return [self._project_research_analysis_summary(analysis) for analysis in analyses]

    def get_research_analysis(self, analysis_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not analysis_id:
            return None
        analysis = (self._local_fallback("research_analyses") or {}).get(analysis_id)
        if not analysis:
            return None
        return self._project_research_analysis_detail(analysis)

    # ------------------------------------------------------------------ #
    # Research Experiments (RW-04)
    # ------------------------------------------------------------------ #

    _RW04_TERMINAL_STATUSES = frozenset({"completed", "failed", "canceled"})
    _RW04_CANCELABLE_STATUSES = frozenset({"queued", "running"})

    @classmethod
    def _rw04_can_cancel(cls, status: Optional[str]) -> bool:
        return str(status or "").strip().lower() in cls._RW04_CANCELABLE_STATUSES

    @classmethod
    def _project_research_experiment_summary(cls, exp: Dict[str, Any]) -> Dict[str, Any]:
        status = str(exp.get("status") or "")
        return {
            "experiment_id": exp.get("experiment_id"),
            "ticket_id": exp.get("ticket_id"),
            "experiment_name": exp.get("experiment_name"),
            "status": status,
            "queued_at": exp.get("queued_at"),
            "started_at": exp.get("started_at"),
            "completed_at": exp.get("completed_at"),
            "artifact_ids": list(exp.get("artifact_ids") or []),
            "allowedActions": {"canCancel": cls._rw04_can_cancel(status)},
        }

    @classmethod
    def _project_research_experiment_detail(cls, exp: Dict[str, Any]) -> Dict[str, Any]:
        status = str(exp.get("status") or "")
        failure = exp.get("failure") or {}
        progress = exp.get("progress") or {}
        strategy_selector = exp.get("strategy_selector") or {}
        run_config = exp.get("run_config") or {}
        time_range = run_config.get("time_range") or {}
        launch_context = exp.get("launch_context") or {}
        return {
            "experiment_id": exp.get("experiment_id"),
            "ticket_id": exp.get("ticket_id"),
            "experiment_name": exp.get("experiment_name"),
            "status": status,
            "queued_at": exp.get("queued_at"),
            "started_at": exp.get("started_at"),
            "completed_at": exp.get("completed_at"),
            "progress": {
                "percent": progress.get("percent"),
                "phase": progress.get("phase"),
                "message": progress.get("message"),
            },
            "strategy_selector": {
                "strategy_id": strategy_selector.get("strategy_id"),
                "variant_id": strategy_selector.get("variant_id"),
            },
            "parameter_set": json.loads(json.dumps(exp.get("parameter_set") or {})),
            "run_config": {
                "dataset_ref": run_config.get("dataset_ref"),
                "time_range": {
                    "start_at": time_range.get("start_at"),
                    "end_at": time_range.get("end_at"),
                },
                "execution_mode": run_config.get("execution_mode"),
                "priority": run_config.get("priority"),
                "requested_by": run_config.get("requested_by"),
            },
            "launch_context": {
                "analysis_refs": (
                    list(launch_context["analysis_refs"])
                    if isinstance(launch_context.get("analysis_refs"), list)
                    else None
                ),
            },
            "validation_warnings": json.loads(json.dumps(exp.get("validation_warnings") or [])),
            "artifact_ids": list(exp.get("artifact_ids") or []),
            "failure": {
                "reason_code": failure.get("reason_code"),
                "message": failure.get("message"),
            },
            "allowedActions": {"canCancel": cls._rw04_can_cancel(status)},
        }

    def _research_experiments_store(self) -> Dict[str, Any]:
        """Return the live experiment store regardless of fallback setting.

        Uses the service-backed snapshot (same on-disk file create/cancel write
        to) so launch→list/detail/cancel round-trip works with fallback=False.
        Falls through to the in-memory dict so experiments created in the same
        process are always visible before the file-cache is flushed.
        """
        available, records = self._service.list_records("research_experiments")
        if available:
            return {str(r.get("experiment_id") or r.get("id") or ""): r for r in records}
        return self._data.get("research_experiments") or {}

    def list_research_experiments(
        self,
        *,
        ticket_id: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        experiments = list(self._research_experiments_store().values())
        if ticket_id:
            experiments = [
                exp for exp in experiments
                if str(exp.get("ticket_id") or "") == str(ticket_id)
            ]
        if status:
            requested_status = str(status).strip().lower()
            experiments = [
                exp for exp in experiments
                if str(exp.get("status") or "").strip().lower() == requested_status
            ]
        experiments.sort(
            key=lambda exp: _parse_rfc3339(exp.get("queued_at")) or datetime.min,
            reverse=True,
        )
        return [self._project_research_experiment_summary(exp) for exp in experiments]

    def get_research_experiment(self, experiment_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not experiment_id:
            return None
        available, record = self._service.record("research_experiments", experiment_id)
        if available:
            return self._project_research_experiment_detail(record) if record else None
        experiment = (self._data.get("research_experiments") or {}).get(experiment_id)
        if not experiment:
            return None
        return self._project_research_experiment_detail(experiment)

    def create_research_experiment(
        self,
        *,
        ticket_id: str,
        experiment_name: str,
        strategy_selector: Dict[str, Any],
        parameter_set: Dict[str, Any],
        run_config: Dict[str, Any],
        launch_context: Dict[str, Any],
        queued_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        experiments = self._data.get("research_experiments") or {}

        timestamp = queued_at or _utc_now_rfc3339()
        date_part = timestamp[:10].replace("-", "")
        experiment_id = f"exp-{date_part}-{len(experiments) + 1:03d}"
        while experiment_id in experiments:
            experiment_id = f"exp-{date_part}-{len(experiments) + 2:03d}"

        record: Dict[str, Any] = {
            "experiment_id": experiment_id,
            "ticket_id": ticket_id,
            "experiment_name": experiment_name,
            "status": "queued",
            "queued_at": timestamp,
            "started_at": None,
            "completed_at": None,
            "progress": {"percent": None, "phase": None, "message": None},
            "strategy_selector": json.loads(json.dumps(strategy_selector)),
            "parameter_set": json.loads(json.dumps(parameter_set)),
            "run_config": json.loads(json.dumps(run_config)),
            "launch_context": json.loads(json.dumps(launch_context)),
            "validation_warnings": [],
            "artifact_ids": [],
            "failure": {"reason_code": None, "message": None},
            "allowedActions": {"canCancel": True},
        }

        if isinstance(self._data.get("research_experiments"), dict):
            self._data["research_experiments"][experiment_id] = record
        else:
            self._data.setdefault("research_experiments", {})[experiment_id] = record
        self._save()
        return self._project_research_experiment_detail(record)

    def cancel_research_experiment(
        self,
        experiment_id: str,
        *,
        completed_at: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        experiments = self._data.get("research_experiments") or {}
        if experiment_id not in experiments:
            return None

        record = dict(json.loads(json.dumps(experiments[experiment_id])))
        status = str(record.get("status") or "").strip().lower()
        if status not in self._RW04_CANCELABLE_STATUSES:
            return None

        record["status"] = "canceled"
        record["completed_at"] = completed_at or _utc_now_rfc3339()
        record["allowedActions"] = {"canCancel": False}

        if isinstance(self._data.get("research_experiments"), dict):
            self._data["research_experiments"][experiment_id] = record
        else:
            self._data.setdefault("research_experiments", {})[experiment_id] = record
        self._save()
        return self._project_research_experiment_detail(record)

    def _read_dataset_records(self, dataset: str) -> List[Dict[str, Any]]:
        if dataset in ServiceBackedReadAdapter._DATASETS:
            available, records = self._service.list_records(dataset)
            if available:
                return list(records)
        local_payload = self._local_fallback(dataset)
        if isinstance(local_payload, dict):
            return [record for record in local_payload.values() if isinstance(record, dict)]
        if isinstance(local_payload, list):
            return [record for record in local_payload if isinstance(record, dict)]
        return []

    def get_research_search_index(self) -> Optional[Dict[str, Any]]:
        adapter: Optional[Dict[str, Any]] = None
        if "research_search_index" in ServiceBackedReadAdapter._DATASETS:
            available, adapter = self._service.record("research_search_index", "rw02-search-index")
            if available and adapter:
                return json.loads(json.dumps(adapter))

        local_index = self._local_fallback("research_search_index") or {}
        if isinstance(local_index, dict):
            adapter = local_index.get("rw02-search-index")
            if isinstance(adapter, dict):
                return json.loads(json.dumps(adapter))

        documents = self._read_dataset_records("research_search_documents")
        if not documents:
            return None

        indexed_match_types = sorted(
            {
                str(document.get("match_type") or "").strip()
                for document in documents
                if str(document.get("match_type") or "").strip()
            }
        )
        latest_update = max(
            (
                _parse_rfc3339(
                    document.get("updated_at")
                    or document.get("indexed_at")
                    or document.get("created_at")
                )
                for document in documents
            ),
            default=None,
        )
        snapshot_at = (
            latest_update.replace(microsecond=0).isoformat().replace("+00:00", "Z")
            if latest_update is not None
            else _utc_now_rfc3339()
        )
        return {
            "adapter_id": "rw02-search-index",
            "snapshot_at": snapshot_at,
            "adapter_state": "fresh",
            "indexed_match_types": indexed_match_types,
            "source_watermarks": {
                "tickets": None,
                "experiments": None,
                "artifacts": None,
            },
        }

    def list_research_search_results(
        self,
        *,
        query: str,
        match_type: str = "all",
        status: Optional[str] = None,
        date_range: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        documents = self._read_dataset_records("research_search_documents")
        if not documents:
            return []

        ticket_status_by_id = {
            str(ticket.get("ticket_id") or ""): str(ticket.get("status") or "")
            for ticket in self._read_dataset_records("research_tickets")
            if str(ticket.get("ticket_id") or "").strip()
        }
        query_terms = [token for token in str(query).strip().lower().split() if token]
        cutoff_days = self._date_range_cutoff_token(date_range)
        reference_now = datetime.now(timezone.utc).replace(tzinfo=None)

        projected: List[Dict[str, Any]] = []
        for document in documents:
            document_match_type = str(document.get("match_type") or "").strip().lower()
            if document_match_type not in {"ticket", "experiment", "artifact"}:
                continue
            if match_type != "all" and document_match_type != match_type:
                continue

            linked_ticket_id = str(document.get("linked_ticket_id") or "").strip()
            linked_ticket_status = ticket_status_by_id.get(linked_ticket_id) or str(
                document.get("linked_ticket_status") or ""
            ).strip()
            if status and linked_ticket_status.lower() != status.lower():
                continue

            updated_at = _parse_rfc3339(
                document.get("updated_at")
                or document.get("indexed_at")
                or document.get("created_at")
            )
            if cutoff_days is not None:
                if updated_at is None:
                    continue
                if (reference_now - updated_at.replace(tzinfo=None)).days >= cutoff_days:
                    continue

            search_text = " ".join(
                [
                    str(document.get("title") or ""),
                    str(document.get("excerpt") or ""),
                    str(document.get("search_text") or ""),
                ]
            ).lower()
            matched_terms = sum(1 for token in query_terms if token in search_text)
            if matched_terms == 0:
                continue

            title_text = str(document.get("title") or "").lower()
            title_hits = sum(1 for token in query_terms if token in title_text)
            base_score = float(document.get("relevance_score") or 0.0)
            score = round(
                min(0.999, max(base_score, base_score + matched_terms * 0.01 + title_hits * 0.015)),
                3,
            )

            links = document.get("links") if isinstance(document.get("links"), dict) else {}
            projected.append(
                {
                    "result_id": str(document.get("result_id") or ""),
                    "match_type": document_match_type,
                    "title": str(document.get("title") or ""),
                    "excerpt": str(document.get("excerpt") or ""),
                    "linked_ticket_id": linked_ticket_id,
                    "relevance_score": score,
                    "links": {
                        "result_detail": str(
                            links.get("result_detail")
                            or (
                                f"/research/tickets/{document.get('result_id')}"
                                if document_match_type == "ticket"
                                else f"/research/{document_match_type}s/{document.get('result_id')}"
                            )
                        ),
                        "linked_ticket_detail": str(
                            links.get("linked_ticket_detail")
                            or f"/research/tickets/{linked_ticket_id}"
                        ),
                    },
                    "_updated_at": updated_at or datetime.min,
                }
            )

        projected.sort(
            key=lambda item: (
                float(item.get("relevance_score") or 0.0),
                item.get("_updated_at") or datetime.min,
            ),
            reverse=True,
        )
        for item in projected:
            item.pop("_updated_at", None)
        return projected

    # ------------------------------------------------------------------ #
    # Incident surfaces (IN-01 – IN-05)
    # ------------------------------------------------------------------ #

    def list_incidents(
        self,
        status: Optional[str] = None,
        severity: Optional[str] = None,
        affected_pool_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        available, incidents = self._service.list_records("incidents")
        if not available:
            incidents = list((self._local_fallback("incidents") or {}).values())
        if status:
            requested_statuses = {
                token.strip().lower()
                for token in status.split(",")
                if token.strip()
            }
            incidents = [
                i for i in incidents
                if str(i.get("status") or "").lower() in requested_statuses
            ]
        if severity:
            incidents = [i for i in incidents if i.get("severity") == severity]
        if affected_pool_id:
            incidents = [i for i in incidents if i.get("capital_pool_id") == affected_pool_id]
        return sorted(incidents, key=lambda x: x.get("created_at", ""), reverse=True)

    def get_incident(self, incident_id: str) -> Optional[Dict[str, Any]]:
        available, raw = self._service.record("incidents", incident_id)
        if available:
            return raw
        return (self._local_fallback("incidents") or {}).get(incident_id)

    def list_postmortems(self, time_range: Optional[str] = None) -> List[Dict[str, Any]]:
        # time_range deferred — v1 returns all postmortems
        available, postmortems = self._service.list_records("postmortems")
        if available:
            return postmortems
        return list((self._local_fallback("postmortems") or {}).values())

    def get_postmortem(self, report_id: str) -> Optional[Dict[str, Any]]:
        available, raw = self._service.record("postmortems", report_id)
        if available:
            return raw
        return (self._local_fallback("postmortems") or {}).get(report_id)

    def get_postmortem_by_incident(self, incident_id: str) -> Optional[Dict[str, Any]]:
        available, postmortems = self._service.list_records("postmortems")
        if not available:
            postmortems = list((self._local_fallback("postmortems") or {}).values())
        for pm in postmortems:
            if pm.get("incident_id") == incident_id:
                return pm
        return None

    def get_kill_switch_status(self) -> Dict[str, Any]:
        ks = self._local_fallback("kill_switch") or {}
        status = str(ks.get("status") or "").lower()
        if status not in {"armed", "triggered", "cooling_down"}:
            safe_mode_status = str(ks.get("safe_mode_status") or "").lower()
            if safe_mode_status in {"cooling_down", "cooldown"}:
                status = "cooling_down"
            elif ks.get("active"):
                status = "triggered"
            else:
                status = "armed"

        active_commands_raw = ks.get("active_commands")
        if active_commands_raw is None:
            active_commands_raw = ks.get("active_freeze_orders", [])
        active_commands: List[str] = []
        for command in active_commands_raw:
            if isinstance(command, str):
                active_commands.append(command)
                continue
            if not isinstance(command, dict):
                continue
            command_id = (
                command.get("command_id")
                or command.get("id")
                or command.get("type")
                or command.get("scope")
            )
            if command_id:
                active_commands.append(str(command_id))

        last_confirmed_at = ks.get("last_confirmed_at") or ks.get("last_checked_at", "")
        return {
            "active": ks.get("active", False),
            "active_freeze_orders": ks.get("active_freeze_orders", []),
            "last_checked_at": ks.get("last_checked_at", ""),
            "safe_mode_status": ks.get("safe_mode_status", "off"),
            "status": status,
            "last_triggered_at": ks.get("last_triggered_at"),
            "last_confirmed_at": last_confirmed_at,
            "active_commands": active_commands,
            "secondary_path_available": ks.get("secondary_path_available", True),
        }

    # ------------------------------------------------------------------ #
    # Composed view helpers
    # ------------------------------------------------------------------ #

    def get_evolution_decision(self, decision_id: str) -> Optional[Dict[str, Any]]:
        available, raw = self._service.record("evolution_decisions", decision_id)
        if available:
            return self._project_service_evolution_decision(raw) if raw else None
        raw = (self._local_fallback("evolution_decisions") or {}).get(decision_id)
        return self._project_service_evolution_decision(raw) if raw else None

    def get_evolution_decisions_by_incident(self, incident_id: str) -> List[Dict[str, Any]]:
        available, raw_decisions = self._service.list_records("evolution_decisions")
        if available:
            decisions = [
                self._project_service_evolution_decision(raw)
                for raw in raw_decisions
            ]
        else:
            decisions = [
                self._project_service_evolution_decision(raw)
                for raw in (self._local_fallback("evolution_decisions") or {}).values()
            ]
        return [
            d for d in decisions
            if d.get("incident_ref") == incident_id
        ]

    def get_rollbacks_by_incident(self, incident_id: str) -> List[Dict[str, Any]]:
        return list((self._local_fallback("rollbacks_by_incident") or {}).get(incident_id, []))

    def get_telemetry_summary(self, runtime_id: str) -> Optional[Dict[str, Any]]:
        available, raw = self._service.record("telemetry_summaries", runtime_id)
        if available:
            return raw
        return (self._local_fallback("telemetry_summaries") or {}).get(runtime_id)

    # ------------------------------------------------------------------ #
    # Evolution surfaces (EV-01 – EV-04)
    # ------------------------------------------------------------------ #

    def list_evolution_decisions(
        self,
        action_type: Optional[str] = None,
        risk_level: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        available, raw_decisions = self._service.list_records("evolution_decisions")
        if available:
            decisions = [
                self._project_service_evolution_decision(raw)
                for raw in raw_decisions
            ]
        else:
            decisions = [
                self._project_service_evolution_decision(raw)
                for raw in (self._local_fallback("evolution_decisions") or {}).values()
            ]
        if action_type:
            decisions = [d for d in decisions if d.get("action_type") == action_type]
        if risk_level:
            decisions = [d for d in decisions if d.get("risk_level") == risk_level]
        if status:
            decisions = [d for d in decisions if d.get("status") == status]
        return sorted(decisions, key=lambda x: x.get("created_at", ""), reverse=True)

    def get_evolution_decision_by_id(self, decision_id: str) -> Optional[Dict[str, Any]]:
        available, raw = self._service.record("evolution_decisions", decision_id)
        if available:
            return self._project_service_evolution_decision(raw) if raw else None
        raw = (self._local_fallback("evolution_decisions") or {}).get(decision_id)
        return self._project_service_evolution_decision(raw) if raw else None

    def list_freeze_orders(
        self,
        status: Optional[str] = None,
        scope: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        orders = list((self._local_fallback("freeze_orders") or {}).values())
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
        rollbacks = list(self._local_fallback("all_rollbacks") or [])
        if runtime_id:
            rollbacks = [r for r in rollbacks if r.get("runtime_id") == runtime_id]
        if action_type:
            rollbacks = [r for r in rollbacks if r.get("action_type") == action_type]
        # time_range filtering deferred in v1
        return sorted(rollbacks, key=lambda x: x.get("initiated_at", ""), reverse=True)

    def get_rollback_review(self, rollback_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not rollback_id:
            return None
        review = (self._local_fallback("rollback_reviews") or {}).get(rollback_id)
        if review:
            return json.loads(json.dumps(review))
        return None

    def list_governance_audit_events(
        self,
        *,
        actor: Optional[str] = None,
        action_types: Optional[List[str]] = None,
        target_type: Optional[str] = None,
        from_ts: Optional[datetime] = None,
        to_ts: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        events = list(self._local_fallback("governance_audit_events") or [])

        if actor:
            events = [event for event in events if event.get("actor") == actor]
        if action_types:
            allowed = {value for value in action_types if value}
            events = [event for event in events if event.get("action_type") in allowed]
        if target_type:
            events = [event for event in events if event.get("target_type") == target_type]

        if from_ts is not None:
            events = [
                event
                for event in events
                if (
                    _parse_rfc3339(event.get("timestamp")) is not None
                    and _parse_rfc3339(event.get("timestamp")) >= from_ts
                )
            ]
        if to_ts is not None:
            events = [
                event
                for event in events
                if (
                    _parse_rfc3339(event.get("timestamp")) is not None
                    and _parse_rfc3339(event.get("timestamp")) <= to_ts
                )
            ]

        events.sort(key=lambda event: event.get("timestamp", ""), reverse=True)
        return json.loads(json.dumps(events))

    # ------------------------------------------------------------------ #
    # Lineage surfaces (LN-01 – LN-03)
    # ------------------------------------------------------------------ #

    @staticmethod
    def _lineage_edge_sort_key(edge: Dict[str, Any]) -> tuple[str, str]:
        return (
            str(edge.get("created_at") or ""),
            str(edge.get("id") or ""),
        )

    def _artifact_metadata_index(self) -> Dict[str, Dict[str, str]]:
        index: Dict[str, Dict[str, str]] = {}

        def merge(
            artifact_id: Any,
            *,
            artifact_version: Any = None,
            artifact_type: Any = None,
        ) -> None:
            key = str(artifact_id or "").strip()
            if not key:
                return
            entry = index.setdefault(
                key,
                {
                    "artifact_id": key,
                    "artifact_version": "",
                    "artifact_type": "",
                },
            )
            if artifact_version not in (None, "") and not entry["artifact_version"]:
                entry["artifact_version"] = str(artifact_version)
            if artifact_type not in (None, "") and not entry["artifact_type"]:
                entry["artifact_type"] = str(artifact_type)

        for entry in self.list_registry_entries():
            merge(
                entry.get("artifact_id") or entry.get("registry_id") or entry.get("id"),
                artifact_version=entry.get("artifact_version") or entry.get("version"),
                artifact_type=entry.get("artifact_type"),
            )

        for plan in self.list_deployment_plans():
            merge(
                plan.get("artifact_id"),
                artifact_version=plan.get("artifact_version"),
                artifact_type=plan.get("artifact_type"),
            )

        for binding in self.list_runtime_bindings():
            merge(
                binding.get("artifact_id"),
                artifact_version=binding.get("artifact_version"),
                artifact_type=binding.get("artifact_type"),
            )

        for incident in self.list_incidents():
            merge(
                incident.get("artifact_id"),
                artifact_version=incident.get("artifact_version"),
                artifact_type=incident.get("artifact_type"),
            )

        available, raw_postmortems = self._service.list_records("postmortems")
        postmortems = raw_postmortems if available else list((self._local_fallback("postmortems") or {}).values())
        for postmortem in postmortems:
            merge(
                postmortem.get("artifact_id"),
                artifact_version=postmortem.get("artifact_version"),
                artifact_type=postmortem.get("artifact_type"),
            )

        available, edges = self._service.list_records("lineage_edges")
        if not available:
            edges = list((self._local_fallback("lineage_edges") or {}).values())
        for edge in edges:
            merge(
                edge.get("from_artifact_id"),
                artifact_version=edge.get("from_artifact_version"),
                artifact_type=edge.get("from_artifact_type"),
            )
            merge(
                edge.get("to_artifact_id"),
                artifact_version=edge.get("to_artifact_version"),
                artifact_type=edge.get("to_artifact_type"),
            )

        return index

    def list_lineage_edges(
        self,
        artifact_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        available, edges = self._service.list_records("lineage_edges")
        if not available:
            edges = list((self._local_fallback("lineage_edges") or {}).values())
        if artifact_id:
            edges = [
                e for e in edges
                if e.get("from_artifact_id") == artifact_id or e.get("to_artifact_id") == artifact_id
            ]
        return sorted(edges, key=self._lineage_edge_sort_key, reverse=True)

    def list_lineage_records(
        self,
        artifact_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        edges = self.list_lineage_edges()
        if artifact_id:
            artifact_edges = [
                edge
                for edge in edges
                if edge.get("from_artifact_id") == artifact_id or edge.get("to_artifact_id") == artifact_id
            ]
            if not artifact_edges:
                return []
            last_edge_at = max(
                (str(edge.get("created_at") or "") for edge in artifact_edges),
                default="",
            )
            return [{
                "artifact_id": artifact_id,
                "edge_count": len(artifact_edges),
                "last_edge_at": last_edge_at,
            }]

        aggregates: Dict[str, Dict[str, Any]] = {}
        for edge in edges:
            for key in {edge.get("from_artifact_id"), edge.get("to_artifact_id")}:
                artifact_key = str(key or "").strip()
                if not artifact_key:
                    continue
                aggregate = aggregates.setdefault(
                    artifact_key,
                    {
                        "artifact_id": artifact_key,
                        "edge_count": 0,
                        "last_edge_at": "",
                    },
                )
                aggregate["edge_count"] += 1
                created_at = str(edge.get("created_at") or "")
                if created_at > str(aggregate.get("last_edge_at") or ""):
                    aggregate["last_edge_at"] = created_at

        items = sorted(aggregates.values(), key=lambda item: str(item.get("artifact_id") or ""))
        items.sort(key=lambda item: str(item.get("last_edge_at") or ""), reverse=True)
        return items

    def get_lineage_edge(self, edge_id: str) -> Optional[Dict[str, Any]]:
        available, raw = self._service.record("lineage_edges", edge_id)
        if available:
            return raw
        return (self._local_fallback("lineage_edges") or {}).get(edge_id)

    @staticmethod
    def _project_inspiration_graph(raw: Dict[str, Any]) -> Dict[str, Any]:
        meta = raw.get("meta") if isinstance(raw.get("meta"), dict) else {}
        surfaces = meta.get("surfaces") if isinstance(meta.get("surfaces"), dict) else {}

        edges: List[Dict[str, Any]] = []
        for edge in raw.get("inspiration_edges") or []:
            if not isinstance(edge, dict):
                continue
            source_artifact_id = str(edge.get("source_artifact_id") or "").strip()
            relationship_type = str(edge.get("relationship_type") or "").strip()
            if not source_artifact_id or not relationship_type:
                continue
            try:
                influence_weight = float(edge.get("influence_weight") or 0.0)
            except (TypeError, ValueError):
                influence_weight = 0.0
            edges.append(
                {
                    "source_artifact_id": source_artifact_id,
                    "relationship_type": relationship_type,
                    "influence_weight": round(min(max(influence_weight, 0.0), 1.0), 3),
                }
            )

        strategy_tags = [
            str(tag).strip()
            for tag in raw.get("strategy_tags") or []
            if str(tag).strip()
        ]

        projection: Dict[str, Any] = {
            "artifact_id": str(raw.get("artifact_id") or raw.get("id") or "").strip(),
            "inspiration_edges": edges,
            "strategy_tags": strategy_tags,
            "meta": {
                "snapshot_at": str(
                    raw.get("snapshot_at")
                    or meta.get("snapshot_at")
                    or _utc_now_rfc3339()
                ),
                "surfaces": {
                    "inspiration": str(
                        surfaces.get("inspiration")
                        or raw.get("surface_state")
                        or "fresh"
                    ),
                },
            },
        }

        page_info = raw.get("page_info") if isinstance(raw.get("page_info"), dict) else {}
        next_page_token = raw.get("next_page_token")
        if next_page_token is None:
            next_page_token = page_info.get("next_page_token")
        if next_page_token not in (None, ""):
            projection["page_info"] = {"next_page_token": str(next_page_token)}

        return projection

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
        available, edges = self._service.list_records("lineage_edges")
        if not available:
            edges = list((self._local_fallback("lineage_edges") or {}).values())
        if root_id:
            edges = [
                e for e in edges
                if e.get("from_artifact_id") == root_id or e.get("to_artifact_id") == root_id
            ]
        if root_type:
            # v1: root_type filtering is a no-op since edges don't carry type metadata
            # Production would filter by artifact type via registry lookup
            pass
        return sorted(edges, key=self._lineage_edge_sort_key, reverse=True)

    def get_lineage_graph_nodes(
        self,
        edges: List[Dict[str, Any]],
    ) -> List[Dict[str, str]]:
        metadata_index = self._artifact_metadata_index()
        artifact_ids = sorted({
            str(edge.get("from_artifact_id") or "")
            for edge in edges
        } | {
            str(edge.get("to_artifact_id") or "")
            for edge in edges
        })
        artifact_ids = [artifact_id for artifact_id in artifact_ids if artifact_id]

        nodes: List[Dict[str, str]] = []
        for artifact_id in artifact_ids:
            metadata = metadata_index.get(
                artifact_id,
                {
                    "artifact_id": artifact_id,
                    "artifact_version": "",
                    "artifact_type": "",
                },
            )
            nodes.append(
                {
                    "artifact_id": artifact_id,
                    "artifact_version": str(metadata.get("artifact_version") or ""),
                    "artifact_type": str(metadata.get("artifact_type") or ""),
                }
            )
        return nodes

    def artifact_exists(self, artifact_id: str) -> bool:
        artifact_key = str(artifact_id or "").strip()
        if not artifact_key:
            return False
        return artifact_key in self._artifact_metadata_index()

    def get_inspiration_graph(self, artifact_id: str) -> Optional[Dict[str, Any]]:
        available, raw = self._service.record("inspiration_graphs", artifact_id)
        if available:
            return self._project_inspiration_graph(raw) if raw else None
        raw = (self._local_fallback("inspiration_graphs") or {}).get(artifact_id)
        return self._project_inspiration_graph(raw) if raw else None

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
        available, summaries = self._service.list_records("telemetry_summaries")
        if available:
            summary_records = [
                (summary.get("runtime_id") or summary.get("id"), summary)
                for summary in summaries
            ]
        else:
            summary_records = list((self._local_fallback("telemetry_summaries") or {}).items())

        for runtime_id, summary in summary_records:
            if not runtime_id:
                continue
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
        available, raw = self._service.record("telemetry_performance", artifact_id)
        if available:
            return raw
        return (self._local_fallback("telemetry_performance") or {}).get(artifact_id)

    def get_paper_live_drift_report(self, runtime_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not runtime_id:
            return None
        available, raw = self._service.record("paper_live_drift_reports", runtime_id)
        if available:
            return json.loads(json.dumps(raw)) if raw else None
        raw = (self._local_fallback("paper_live_drift_reports") or {}).get(runtime_id)
        return json.loads(json.dumps(raw)) if raw else None

    # ------------------------------------------------------------------ #
    # Persona session surfaces (PS-03, PS-05)
    # ------------------------------------------------------------------ #

    def get_sessions_for_persona(self, persona_id: Optional[str]) -> Optional[List[Dict[str, Any]]]:
        """PS-03: Return active sessions for a persona.

        Returns None when the persona cannot be verified (degraded mode).
        """
        if not persona_id:
            return None
        if self.get_persona(persona_id) is None:
            return None
        available, raw_sessions = self._service.list_records("sessions")
        if available:
            return [
                self._project_service_session(session)
                for session in raw_sessions
                if session.get("persona_id") == persona_id
            ]
        return [
            s for s in (self._local_fallback("sessions") or {}).values()
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
        available, raw = self._service.record("sessions", session_id)
        if available:
            return self._project_service_session(raw) if raw else None
        return (self._local_fallback("sessions") or {}).get(session_id)

    def get_teaching_sessions_for_persona(self, persona_id: Optional[str]) -> Optional[List[Dict[str, Any]]]:
        """PS-05: Return teaching sessions for a persona.

        Returns None when the persona cannot be verified (degraded mode).
        """
        if not persona_id:
            return None
        if self.get_persona(persona_id) is None:
            return None
        available, raw_sessions = self._service.list_records("teaching_sessions")
        if available:
            return [
                session
                for session in raw_sessions
                if session.get("persona_id") == persona_id
            ]
        return [
            s for s in (self._local_fallback("teaching_sessions") or {}).values()
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

    @staticmethod
    def _trainer_session_allowed_actions(status: Optional[str]) -> Dict[str, bool]:
        return {
            "canSendMessage": str(status or "").strip().lower() == "active",
        }

    def _trainer_actor_context(self, session: Dict[str, Any]) -> Dict[str, Optional[str]]:
        explicit = session.get("actor_context")
        if isinstance(explicit, dict):
            return {
                "persona_display_name": explicit.get("persona_display_name"),
                "persona_role_context": explicit.get("persona_role_context"),
            }

        persona = self.get_persona(session.get("persona_id"))
        if persona:
            return {
                "persona_display_name": persona.get("name"),
                "persona_role_context": persona.get("mandate") or persona.get("strategy_family"),
            }
        return {
            "persona_display_name": None,
            "persona_role_context": None,
        }

    @staticmethod
    def _project_teaching_event(session_id: str, event: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "event_id": event.get("event_id"),
            "session_id": session_id,
            "actor": event.get("actor"),
            "message_body": event.get("message_body"),
            "emitted_at": event.get("emitted_at"),
            "sequence_number": event.get("sequence_number"),
            "outcome_signal": event.get("outcome_signal"),
        }

    def _project_trainer_session_summary(self, session: Dict[str, Any]) -> Dict[str, Any]:
        session_id = str(session.get("session_id") or session.get("id") or "")
        events = sorted(
            [
                self._project_teaching_event(session_id, event)
                for event in (session.get("events") or [])
                if isinstance(event, dict)
            ],
            key=lambda item: int(item.get("sequence_number") or 0),
        )
        latest_outcome_signal = None
        for event in events:
            if event.get("outcome_signal") not in (None, ""):
                latest_outcome_signal = event.get("outcome_signal")
        last_event_at = events[-1].get("emitted_at") if events else None
        return {
            "message_count": len(events),
            "last_event_at": last_event_at,
            "latest_outcome_signal": latest_outcome_signal,
        }

    def _project_trainer_session_list_item(self, session: Dict[str, Any]) -> Dict[str, Any]:
        session_id = str(session.get("session_id") or session.get("id") or "")
        summary = self._project_trainer_session_summary(session)
        status = session.get("status")
        return {
            "session_id": session_id,
            "persona_id": session.get("persona_id"),
            "session_type": session.get("session_type") or "trainer",
            "objective": session.get("objective") or session.get("topic"),
            "status": status,
            "started_at": session.get("started_at"),
            "ended_at": session.get("ended_at") or session.get("completed_at"),
            "message_count": summary["message_count"],
            "last_event_at": summary["last_event_at"],
            "latest_outcome_signal": summary["latest_outcome_signal"],
            "actor_context": self._trainer_actor_context(session),
            "allowedActions": self._trainer_session_allowed_actions(status),
            "links": {
                "workbench_detail": f"/trainer/sessions/{session_id}",
            },
        }

    def _project_trainer_session_detail(self, session: Dict[str, Any]) -> Dict[str, Any]:
        session_id = str(session.get("session_id") or session.get("id") or "")
        events = sorted(
            [
                self._project_teaching_event(session_id, event)
                for event in (session.get("events") or [])
                if isinstance(event, dict)
            ],
            key=lambda item: int(item.get("sequence_number") or 0),
        )
        status = session.get("status")
        return {
            "session_id": session_id,
            "persona_id": session.get("persona_id"),
            "session_type": session.get("session_type") or "trainer",
            "objective": session.get("objective") or session.get("topic"),
            "status": status,
            "started_at": session.get("started_at"),
            "ended_at": session.get("ended_at") or session.get("completed_at"),
            "opened_by": session.get("opened_by") or session.get("operator_id"),
            "context_refs": json.loads(json.dumps(session.get("context_refs") or [])),
            "actor_context": self._trainer_actor_context(session),
            "session_summary": self._project_trainer_session_summary(session),
            "events": events,
            "allowedActions": self._trainer_session_allowed_actions(status),
            "links": {
                "self": f"/api/v1/trainer/sessions/{session_id}",
                "workbench_detail": f"/trainer/sessions/{session_id}",
            },
        }

    def list_trainer_sessions(
        self,
        *,
        persona_id: Optional[str],
        status: Optional[str] = None,
    ) -> Optional[List[Dict[str, Any]]]:
        sessions = self.get_teaching_sessions_for_persona(persona_id)
        if sessions is None:
            return None
        items = [
            self._project_trainer_session_list_item(session)
            for session in sessions
            if str(session.get("session_type") or session.get("mode") or "").strip().lower() == "trainer"
        ]
        if status:
            normalized = str(status).strip().lower()
            items = [item for item in items if str(item.get("status") or "").strip().lower() == normalized]
        items.sort(
            key=lambda item: (
                _parse_rfc3339(item.get("last_event_at"))
                or _parse_rfc3339(item.get("started_at"))
                or datetime.min
            ),
            reverse=True,
        )
        return items

    def get_trainer_session(self, session_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not session_id:
            return None
        available, raw = self._service.record("teaching_sessions", session_id)
        if available:
            if raw is None:
                return None
            normalized = dict(raw)
        else:
            teaching_sessions = self._local_fallback("teaching_sessions") or {}
            normalized = teaching_sessions.get(session_id)
            if normalized is None:
                return None
        session_type = str(normalized.get("session_type") or normalized.get("mode") or "").strip().lower()
        if session_type != "trainer":
            return None
        return self._project_trainer_session_detail(normalized)

    def create_trainer_session(
        self,
        *,
        persona_id: str,
        objective: str,
        context_refs: List[Dict[str, Any]],
        actor_id: str,
        created_at: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        service_store_path = self._service._resolve_path("teaching_sessions")
        persist_service_store = service_store_path is not None
        teaching_sessions: Optional[Dict[str, Dict[str, Any]]]
        if persist_service_store:
            available, service_sessions = self._service.list_records("teaching_sessions")
            if not available and service_store_path.exists():
                return None
            teaching_sessions = {
                str(session.get("session_id") or session.get("id") or ""): json.loads(
                    json.dumps(session)
                )
                for session in service_sessions
                if isinstance(session, dict)
                and str(session.get("session_id") or session.get("id") or "").strip()
            }
        else:
            teaching_sessions = self._local_fallback("teaching_sessions")
        if teaching_sessions is None:
            return None

        timestamp = created_at or _utc_now_rfc3339()
        prefix = timestamp[:10].replace("-", "")
        next_index = len(teaching_sessions) + 1
        session_id = f"trn-{prefix}-{next_index:03d}"
        while session_id in teaching_sessions:
            next_index += 1
            session_id = f"trn-{prefix}-{next_index:03d}"

        persona = self.get_persona(persona_id)
        session = {
            "id": session_id,
            "session_id": session_id,
            "persona_id": persona_id,
            "session_type": "trainer",
            "objective": objective,
            "status": "active",
            "started_at": timestamp,
            "ended_at": None,
            "opened_by": actor_id,
            "context_refs": json.loads(json.dumps(context_refs)),
            "actor_context": {
                "persona_display_name": (persona or {}).get("name"),
                "persona_role_context": (persona or {}).get("mandate") or (persona or {}).get("strategy_family"),
            },
            "events": [],
            "topic": objective,
            "outcomes": [],
        }
        teaching_sessions[session_id] = session
        if persist_service_store:
            self._service.write_records("teaching_sessions", teaching_sessions)
        else:
            self._save()
        return self._project_trainer_session_detail(session)

    def append_trainer_message(
        self,
        session_id: str,
        *,
        message_body: str,
        actor_id: str,
        accepted_at: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        service_store_path = self._service._resolve_path("teaching_sessions")
        persist_service_store = service_store_path is not None
        teaching_sessions: Optional[Dict[str, Dict[str, Any]]]
        if persist_service_store:
            available, service_sessions = self._service.list_records("teaching_sessions")
            if not available and service_store_path.exists():
                return None
            teaching_sessions = {
                str(session.get("session_id") or session.get("id") or ""): json.loads(
                    json.dumps(session)
                )
                for session in service_sessions
                if isinstance(session, dict)
                and str(session.get("session_id") or session.get("id") or "").strip()
            }
        else:
            teaching_sessions = self._local_fallback("teaching_sessions")
        if teaching_sessions is None:
            return None

        session = teaching_sessions.get(session_id)
        if session is None:
            return None

        timestamp = accepted_at or _utc_now_rfc3339()
        events = session.setdefault("events", [])
        next_sequence = max((int(event.get("sequence_number") or 0) for event in events), default=0) + 1
        prefix = timestamp[:10].replace("-", "")
        event_id = f"tevt-{prefix}-{next_sequence:03d}"
        existing_ids = {str(event.get("event_id") or "") for event in events if isinstance(event, dict)}
        dedupe_index = next_sequence
        while event_id in existing_ids:
            dedupe_index += 1
            event_id = f"tevt-{prefix}-{dedupe_index:03d}"

        event = {
            "event_id": event_id,
            "session_id": session_id,
            "actor": "operator",
            "message_body": message_body,
            "emitted_at": timestamp,
            "sequence_number": next_sequence,
            "outcome_signal": None,
        }
        events.append(event)
        if persist_service_store:
            self._service.write_records("teaching_sessions", teaching_sessions)
        else:
            self._save()

        projected = self._project_trainer_session_detail(session)
        return {
            "accepted_at": timestamp,
            "event": self._project_teaching_event(session_id, event),
            "session": projected,
        }

    def get_capability_snapshot(self, snapshot_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not snapshot_id:
            return None
        available, raw = self._service.record("capability_snapshots", snapshot_id)
        if available:
            return raw
        return (self._local_fallback("capability_snapshots") or {}).get(snapshot_id)

    def get_capability_snapshot_for_persona(self, persona_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not persona_id:
            return None
        available, snapshots = self._service.list_records("capability_snapshots")
        if available:
            for snapshot in snapshots:
                if snapshot.get("persona_id") == persona_id:
                    return snapshot
        for snapshot in (self._local_fallback("capability_snapshots") or {}).values():
            if snapshot.get("persona_id") == persona_id:
                return snapshot
        return None

    # ------------------------------------------------------------------ #
    # Consultation surfaces (CS-01 – CS-06)
    # ------------------------------------------------------------------ #

    def _consultation_session_records(self) -> Dict[str, Dict[str, Any]]:
        available, sessions = self._service.list_records("consultation_sessions")
        if available:
            return {
                str(session_id): session
                for session in sessions
                if isinstance(session, dict)
                for session_id in [session.get("session_id") or session.get("id")]
                if session_id
            }
        return self._local_fallback("consultation_sessions") or {}

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
        if self.get_persona(persona_id) is None:
            return None
        all_sessions = self._consultation_session_records()
        sessions = [
            s for s in all_sessions.values()
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
        session = self._consultation_session_records().get(session_id)
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
        session = self._consultation_session_records().get(session_id)
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
        all_sessions = self._consultation_session_records()
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
        all_sessions = self._consultation_session_records()
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
        all_sessions = self._consultation_session_records()
        if session_id not in all_sessions:
            return None
        root_id = self._resolve_root_consultation_id(session_id)
        session = self.get_consultation(root_id)
        if session is None:
            return None
        meta_consult = (session.get("metadata") or {}).get("consultation", {})
        return list(meta_consult.get("evidence_refs") or [])

    @staticmethod
    def _committee_surface_state(root_session: Dict[str, Any]) -> str:
        consult = (root_session.get("metadata") or {}).get("consultation", {})
        return str(consult.get("committee_surface_state") or "ok")

    @staticmethod
    def _committee_linked_request_id(root_session: Dict[str, Any]) -> Optional[str]:
        return root_session.get("request_id")

    @classmethod
    def _committee_board_row(
        cls,
        root_session: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        consult = (root_session.get("metadata") or {}).get("consultation", {})
        committee_id = str(consult.get("committee_ref") or "").strip()
        committee_session_ids = list(consult.get("committee_session_ids") or [])
        if not committee_id or not committee_session_ids:
            return None
        return {
            "committee_id": committee_id,
            "committee_ref": committee_id,
            "escalation_reason": json.loads(json.dumps(consult.get("escalation_reason") or {})),
            "quorum_state": consult.get("quorum_state"),
            "consensus_state": consult.get("consensus_state"),
            "linked_request_id": cls._committee_linked_request_id(root_session),
            "started_at": consult.get("committee_started_at") or root_session.get("started_at"),
            "surface_state": cls._committee_surface_state(root_session),
            "route_href": f"/consultation/committees/{committee_id}",
        }

    def list_committees(
        self,
        *,
        quorum_states: Optional[List[str]] = None,
        consensus_states: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for session in self._consultation_session_records().values():
            if session.get("session_type") != "consult":
                continue
            row = self._committee_board_row(session)
            if row is None:
                continue
            rows.append(row)

        if quorum_states:
            requested = {str(value).strip().lower() for value in quorum_states if str(value).strip()}
            rows = [
                row
                for row in rows
                if str(row.get("quorum_state") or "").strip().lower() in requested
            ]
        if consensus_states:
            requested = {str(value).strip().lower() for value in consensus_states if str(value).strip()}
            rows = [
                row
                for row in rows
                if str(row.get("consensus_state") or "").strip().lower() in requested
            ]

        rows.sort(
            key=lambda row: _parse_rfc3339(row.get("started_at")) or datetime.min,
            reverse=True,
        )
        return rows

    def get_committee(self, committee_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not committee_id:
            return None

        root_session: Optional[Dict[str, Any]] = None
        for session in self._consultation_session_records().values():
            if session.get("session_type") != "consult":
                continue
            consult = (session.get("metadata") or {}).get("consultation", {})
            if str(consult.get("committee_ref") or "") == str(committee_id):
                root_session = session
                break
        if root_session is None:
            return None

        consult = (root_session.get("metadata") or {}).get("consultation", {})
        committee_session_ids = list(consult.get("committee_session_ids") or [])
        all_sessions = self._consultation_session_records()
        participant_roster: List[Dict[str, Any]] = []
        sponsor_session_id = str(consult.get("sponsor_session_id") or "").strip()

        for session_id in committee_session_ids:
            participant = all_sessions.get(session_id)
            if not participant:
                continue
            participant_consult = (participant.get("metadata") or {}).get("consultation", {})
            persona = self.get_persona(participant.get("persona_id"))
            participant_roster.append(
                {
                    "participant_id": participant.get("session_id"),
                    "persona_id": participant.get("persona_id"),
                    "persona_label": (persona or {}).get("name"),
                    "role": "sponsor" if participant.get("session_id") == sponsor_session_id else (
                        participant_consult.get("role") or "committee_participant"
                    ),
                    "status": participant_consult.get("participant_status") or participant.get("status"),
                    "outcome_signal": participant_consult.get("outcome_signal"),
                    "rationale_ref": participant_consult.get("rationale_ref"),
                }
            )

        sponsor_assignment = next(
            (row for row in participant_roster if row.get("role") == "sponsor"),
            None,
        )
        board_row = self._committee_board_row(root_session)
        if board_row is None:
            return None

        return {
            **board_row,
            "linked_session_id": root_session.get("session_id"),
            "participant_roster": participant_roster,
            "sponsor_assignment": sponsor_assignment,
            "sponsor_decision": consult.get("sponsor_decision"),
            "sponsor_decided_at": consult.get("sponsor_decided_at"),
            "sponsor_decided_by": consult.get("sponsor_decided_by"),
            "synthesis_summary": json.loads(json.dumps(consult.get("synthesis_summary") or {})),
            "linked_evidence": json.loads(json.dumps(consult.get("evidence_refs") or [])),
        }

    def record_sponsor_decision(
        self,
        committee_id: str,
        *,
        sponsor_decision: str,
        rationale_ref: str,
        actor_id: str,
        recorded_at: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        consultation_sessions = self._local_fallback("consultation_sessions")
        if consultation_sessions is None:
            return None

        root_session_id: Optional[str] = None
        for session_id, session in consultation_sessions.items():
            consult = (session.get("metadata") or {}).get("consultation", {})
            if (
                session.get("session_type") == "consult"
                and str(consult.get("committee_ref") or "") == str(committee_id)
            ):
                root_session_id = session_id
                break
        if root_session_id is None:
            return None

        timestamp = recorded_at or _utc_now_rfc3339()
        consult = consultation_sessions[root_session_id].setdefault("metadata", {}).setdefault("consultation", {})
        consult["sponsor_decision"] = sponsor_decision
        consult["sponsor_decided_at"] = timestamp
        consult["sponsor_decided_by"] = actor_id
        consult["consensus_state"] = "reached"
        consult["outcome"] = sponsor_decision
        synthesis_summary = dict(consult.get("synthesis_summary") or {})
        synthesis_summary["outcome"] = sponsor_decision
        synthesis_summary["rationale_ref"] = rationale_ref
        consult["synthesis_summary"] = synthesis_summary
        consult["rationale_ref"] = rationale_ref

        self._save()
        return self.get_committee(committee_id)

    def get_consult_policy(self, persona_id: Optional[str]) -> Optional[Dict[str, Any]]:
        """CS-06: Return the ConsultPolicy for a persona."""
        if not persona_id:
            return None
        available, raw = self._service.record("consult_policies", persona_id)
        if available:
            return raw
        return (self._local_fallback("consult_policies") or {}).get(persona_id)

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
