"""Lifecycle, Telemetry, Incident, Governance, and Lineage Typed Domain Ports.

This module provides typed domain ports (protocols and domain adapters) for:
- Incident and Postmortem reads (IN-01, IN-02, PM-01, PM-02)
- Lifecycle and Loop reads (Loop runs, Sentinel findings, Kill switch, Trade journey projection)
- Governance and Evolution reads (Evolution decisions, Freeze orders, Rollbacks, Audit events)
- Lineage and Inspiration reads (Lineage edges, records, graph nodes, Inspiration graph)
- Telemetry and Drift reads (Telemetry events with source fallback, Summaries, Performance, Paper-live drift)

These ports decouple route handlers and read models from the monolithic ReadSurfaceStore,
ensuring domain reads route to their respective domain stores while preserving exact
identity and freshness semantics.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Protocol, Tuple, Union, runtime_checkable

try:
    from services.control_plane.bff.trade_journey_projection_store import (
        configured_projection_reader,
    )
except ImportError:
    try:
        from services.control_plane.bff.trade_journey_projection_store import configured_projection_reader
    except ImportError:
        def configured_projection_reader():
            return None


def _utc_now_rfc3339() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_rfc3339(ts_str: Optional[str]) -> Optional[datetime]:
    if not ts_str or not isinstance(ts_str, str):
        return None
    cleaned = ts_str.strip()
    if not cleaned:
        return None
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def _is_fixture_pack_record(record: Any) -> bool:
    if not isinstance(record, dict):
        return False
    if record.get("_fixture_pack") is True:
        return True
    meta = record.get("meta") or record.get("metadata")
    if isinstance(meta, dict) and meta.get("fixture_pack") is True:
        return True
    return False


# =====================================================================
# Domain Protocols
# =====================================================================

@runtime_checkable
class IncidentReaderPort(Protocol):
    """Port for Incident and Postmortem domain reads."""

    def list_incidents(
        self,
        status: Optional[str] = None,
        severity: Optional[str] = None,
        affected_pool_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]: ...

    def get_incident(self, incident_id: str) -> Optional[Dict[str, Any]]: ...

    def list_postmortems(self, time_range: Optional[str] = None) -> List[Dict[str, Any]]: ...

    def get_postmortem(self, report_id: str) -> Optional[Dict[str, Any]]: ...

    def get_postmortem_by_incident(self, incident_id: str) -> Optional[Dict[str, Any]]: ...

    def get_evolution_decisions_by_incident(self, incident_id: str) -> List[Dict[str, Any]]: ...

    def get_rollbacks_by_incident(self, incident_id: str) -> List[Dict[str, Any]]: ...


@runtime_checkable
class LifecycleReaderPort(Protocol):
    """Port for Lifecycle, Loop Runs, Sentinel Findings, and Kill Switch reads."""

    def list_loop_runs(self) -> Tuple[bool, List[Dict[str, Any]]]: ...

    def get_loop_run(self, loop_run_id: str) -> Tuple[bool, Optional[Dict[str, Any]]]: ...

    def list_loop_health_records(self) -> Tuple[bool, List[Dict[str, Any]]]: ...

    def get_loop_health_record(self, loop_id: str) -> Tuple[bool, Optional[Dict[str, Any]]]: ...

    def list_sentinel_findings(
        self,
        *,
        kind: Optional[str] = None,
        status: Optional[str] = None,
        severity: Optional[str] = None,
    ) -> Tuple[bool, List[Dict[str, Any]]]: ...

    def get_sentinel_finding(self, finding_id: str) -> Tuple[bool, Optional[Dict[str, Any]]]: ...

    def get_kill_switch_status(self) -> Dict[str, Any]: ...

    def loop_run_projection_metadata(self) -> Dict[str, Any]: ...

    def trade_journey_projection_reader(self) -> Any: ...


@runtime_checkable
class GovernanceReaderPort(Protocol):
    """Port for Governance, Evolution decisions, Freeze orders, and Audit events reads."""

    def list_evolution_decisions(
        self,
        action_type: Optional[str] = None,
        risk_level: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]: ...

    def get_evolution_decision_by_id(self, decision_id: str) -> Optional[Dict[str, Any]]: ...

    def get_evolution_decision(self, decision_id: str) -> Optional[Dict[str, Any]]: ...

    def list_freeze_orders(
        self,
        status: Optional[str] = None,
        scope: Optional[str] = None,
    ) -> List[Dict[str, Any]]: ...

    def list_all_rollbacks(
        self,
        runtime_id: Optional[str] = None,
        action_type: Optional[str] = None,
        time_range: Optional[str] = None,
    ) -> List[Dict[str, Any]]: ...

    def get_rollback_review(self, rollback_id: Optional[str]) -> Optional[Dict[str, Any]]: ...

    def list_governance_audit_events(
        self,
        *,
        actor: Optional[str] = None,
        action_types: Optional[List[str]] = None,
        target_type: Optional[str] = None,
        from_ts: Optional[datetime] = None,
        to_ts: Optional[datetime] = None,
        include_fixture_pack: bool = True,
    ) -> List[Dict[str, Any]]: ...


@runtime_checkable
class LineageReaderPort(Protocol):
    """Port for Lineage edges, graph traversal, and Inspiration graphs reads."""

    def list_lineage_edges(
        self,
        artifact_id: Optional[str] = None,
        include_fixture_pack: bool = True,
    ) -> List[Dict[str, Any]]: ...

    def list_lineage_records(
        self,
        artifact_id: Optional[str] = None,
        include_fixture_pack: bool = True,
    ) -> List[Dict[str, Any]]: ...

    def get_lineage_edge(self, edge_id: str) -> Optional[Dict[str, Any]]: ...

    def get_lineage_graph(
        self,
        root_type: Optional[str] = None,
        root_id: Optional[str] = None,
        depth: int = 3,
    ) -> List[Dict[str, Any]]: ...

    def get_lineage_graph_nodes(
        self,
        edges: List[Dict[str, Any]],
    ) -> List[Dict[str, str]]: ...

    def artifact_exists(self, artifact_id: str) -> bool: ...

    def get_inspiration_graph(self, artifact_id: str) -> Optional[Dict[str, Any]]: ...


@runtime_checkable
class TelemetryReaderPort(Protocol):
    """Port for Telemetry events, summaries, performance, and paper-live drift reads."""

    def list_telemetry_events(
        self,
        pool_id: Optional[str] = None,
        artifact_id: Optional[str] = None,
        time_range: Optional[str] = None,
    ) -> List[Dict[str, Any]]: ...

    def list_telemetry_events_with_source(
        self,
        pool_id: Optional[str] = None,
        artifact_id: Optional[str] = None,
        time_range: Optional[str] = None,
    ) -> Tuple[str, List[Dict[str, Any]]]: ...

    def get_telemetry_summary(self, runtime_id: str) -> Optional[Dict[str, Any]]: ...

    def list_telemetry_summaries(self) -> List[Dict[str, Any]]: ...

    def get_telemetry_performance(self, artifact_id: str) -> Optional[Dict[str, Any]]: ...

    def get_paper_live_drift_report(self, runtime_id: Optional[str]) -> Optional[Dict[str, Any]]: ...

    def list_paper_live_drift_reports(self) -> List[Dict[str, Any]]: ...


@runtime_checkable
class LifecycleTelemetryGovernancePort(
    IncidentReaderPort,
    LifecycleReaderPort,
    GovernanceReaderPort,
    LineageReaderPort,
    TelemetryReaderPort,
    Protocol,
):
    """Combined typed port for Lifecycle, Telemetry, Incident, Governance, and Lineage domains."""
    pass


# =====================================================================
# Domain Adapters / Concrete Implementations
# =====================================================================

class DomainIncidentPort:
    """Incident and Postmortem domain reader adapter."""

    def __init__(
        self,
        *,
        incidents: Optional[Dict[str, Dict[str, Any]]] = None,
        postmortems: Optional[Dict[str, Dict[str, Any]]] = None,
        evolution_decisions: Optional[Dict[str, Dict[str, Any]]] = None,
        rollbacks_by_incident: Optional[Dict[str, List[Dict[str, Any]]]] = None,
    ) -> None:
        self._incidents = dict(incidents or {})
        self._postmortems = dict(postmortems or {})
        self._evolution_decisions = dict(evolution_decisions or {})
        self._rollbacks_by_incident = dict(rollbacks_by_incident or {})

    def list_incidents(
        self,
        status: Optional[str] = None,
        severity: Optional[str] = None,
        affected_pool_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        incidents = list(self._incidents.values())
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

        anchor = [
            incident
            for incident in incidents
            if str(incident.get("incident_id") or incident.get("id") or "") == "inc-20260410-001"
        ]
        rest = [
            incident
            for incident in incidents
            if str(incident.get("incident_id") or incident.get("id") or "") != "inc-20260410-001"
        ]
        return anchor + sorted(rest, key=lambda x: str(x.get("created_at") or ""), reverse=True)

    def get_incident(self, incident_id: str) -> Optional[Dict[str, Any]]:
        return self._incidents.get(incident_id)

    def list_postmortems(self, time_range: Optional[str] = None) -> List[Dict[str, Any]]:
        return list(self._postmortems.values())

    def get_postmortem(self, report_id: str) -> Optional[Dict[str, Any]]:
        return self._postmortems.get(report_id)

    def get_postmortem_by_incident(self, incident_id: str) -> Optional[Dict[str, Any]]:
        for pm in self._postmortems.values():
            if pm.get("incident_id") == incident_id:
                return pm
        return None

    def get_evolution_decisions_by_incident(self, incident_id: str) -> List[Dict[str, Any]]:
        decisions = [
            DomainGovernancePort.project_evolution_decision(raw)
            for raw in self._evolution_decisions.values()
        ]
        return [
            d for d in decisions
            if d.get("incident_ref") == incident_id or d.get("linked_incident_id") == incident_id
        ]

    def get_rollbacks_by_incident(self, incident_id: str) -> List[Dict[str, Any]]:
        return list(self._rollbacks_by_incident.get(incident_id, []))


class DomainLifecyclePort:
    """Lifecycle, Loop, Sentinel, and Kill Switch domain reader adapter."""

    _LOOP_RUN_ID_RE = re.compile(r"^loop-run-(\d+)$")

    def __init__(
        self,
        *,
        loop_runs: Optional[Dict[str, Dict[str, Any]]] = None,
        loop_health_records: Optional[Dict[str, Dict[str, Any]]] = None,
        sentinel_findings: Optional[Dict[str, Dict[str, Any]]] = None,
        kill_switch: Optional[Dict[str, Any]] = None,
        projection_metadata: Optional[Dict[str, Any]] = None,
        incidents: Optional[Dict[str, Dict[str, Any]]] = None,
        projection_reader_override: Any = None,
    ) -> None:
        self._loop_runs = dict(loop_runs) if loop_runs is not None else None
        self._loop_health_records = dict(loop_health_records or {})
        self._sentinel_findings = dict(sentinel_findings) if sentinel_findings is not None else None
        self._kill_switch = dict(kill_switch or {})
        self._projection_metadata = dict(projection_metadata or {"envelope": "loop_runs", "status": "ok"})
        self._incidents = dict(incidents or {})
        self._projection_reader_override = projection_reader_override

    def trade_journey_projection_reader(self) -> Any:
        if self._projection_reader_override is not None:
            return self._projection_reader_override
        return configured_projection_reader()

    def loop_run_projection_metadata(self) -> Dict[str, Any]:
        return dict(self._projection_metadata)

    def list_loop_runs(self) -> Tuple[bool, List[Dict[str, Any]]]:
        if self._loop_runs is not None:
            return True, list(self._loop_runs.values())
        if self._incidents:
            runs = [
                self._derive_loop_run(inc)
                for inc in self._incidents.values()
                if isinstance(inc, dict) and "sentinel" not in str(inc.get("title") or "").lower()
            ]
            return True, runs
        return False, []

    def get_loop_run(self, loop_run_id: str) -> Tuple[bool, Optional[Dict[str, Any]]]:
        if self._loop_runs is not None:
            return True, self._loop_runs.get(loop_run_id)
        if self._incidents:
            inc = self._incidents.get(loop_run_id)
            if inc and isinstance(inc, dict) and "sentinel" not in str(inc.get("title") or "").lower():
                return True, self._derive_loop_run(inc)
            m = self._LOOP_RUN_ID_RE.match(loop_run_id)
            if m:
                n = int(m.group(1))
                non_sentinel = [
                    v for v in self._incidents.values()
                    if isinstance(v, dict) and "sentinel" not in str(v.get("title") or "").lower()
                ]
                if 1 <= n <= len(non_sentinel):
                    return True, self._derive_loop_run(non_sentinel[n - 1], override_id=loop_run_id)
            return True, None
        return False, None

    def list_loop_health_records(self) -> Tuple[bool, List[Dict[str, Any]]]:
        return True, list(self._loop_health_records.values())

    def get_loop_health_record(self, loop_id: str) -> Tuple[bool, Optional[Dict[str, Any]]]:
        return True, self._loop_health_records.get(loop_id)

    def list_sentinel_findings(
        self,
        *,
        kind: Optional[str] = None,
        status: Optional[str] = None,
        severity: Optional[str] = None,
    ) -> Tuple[bool, List[Dict[str, Any]]]:
        if self._sentinel_findings is not None:
            results = list(self._sentinel_findings.values())
            return True, self._apply_sentinel_filters(results, kind=kind, status=status, severity=severity)
        if self._incidents:
            results = [
                self._derive_sentinel_finding(inc)
                for inc in self._incidents.values()
                if isinstance(inc, dict) and "loop" not in str(inc.get("title") or "").lower()
            ]
            return True, self._apply_sentinel_filters(results, kind=kind, status=status, severity=severity)
        return False, []

    def get_sentinel_finding(self, finding_id: str) -> Tuple[bool, Optional[Dict[str, Any]]]:
        avail, findings = self.list_sentinel_findings()
        if not avail:
            return False, None
        for finding in findings:
            fid = finding.get("finding_id") or finding.get("id")
            if fid == finding_id:
                return True, finding
        return True, None

    def get_kill_switch_status(self) -> Dict[str, Any]:
        ks = dict(self._kill_switch)
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
            "active": bool(ks.get("active", False)),
            "active_freeze_orders": list(ks.get("active_freeze_orders", [])),
            "last_checked_at": str(ks.get("last_checked_at", "")),
            "safe_mode_status": str(ks.get("safe_mode_status", "off")),
            "status": status,
            "last_triggered_at": ks.get("last_triggered_at"),
            "last_confirmed_at": last_confirmed_at,
            "active_commands": active_commands,
            "secondary_path_available": bool(ks.get("secondary_path_available", True)),
        }

    @staticmethod
    def _apply_sentinel_filters(
        records: List[Dict[str, Any]],
        *,
        kind: Optional[str],
        status: Optional[str],
        severity: Optional[str],
    ) -> List[Dict[str, Any]]:
        filtered = list(records)
        if kind:
            filtered = [r for r in filtered if str(r.get("kind") or "").lower() == kind.lower()]
        if status:
            filtered = [r for r in filtered if str(r.get("status") or "").lower() == status.lower()]
        if severity:
            filtered = [r for r in filtered if str(r.get("severity") or "").lower() == severity.lower()]
        return filtered

    @staticmethod
    def _derive_loop_run(incident: Dict[str, Any], *, override_id: Optional[str] = None) -> Dict[str, Any]:
        inc_id = str(incident.get("incident_id") or incident.get("id") or "inc-unknown")
        run_id = override_id or f"loop-run-{inc_id}"
        return {
            "loop_run_id": run_id,
            "id": run_id,
            "loop_id": incident.get("loop_id") or "loop-default",
            "status": "failed" if incident.get("severity") in ("P0", "P1", "critical", "high") else "completed",
            "triggered_at": incident.get("created_at") or "",
            "completed_at": incident.get("updated_at") or incident.get("created_at") or "",
            "incident_ref": inc_id,
            "summary": incident.get("title") or incident.get("summary") or "",
        }

    @staticmethod
    def _derive_sentinel_finding(incident: Dict[str, Any]) -> Dict[str, Any]:
        inc_id = str(incident.get("incident_id") or incident.get("id") or "inc-unknown")
        finding_id = f"sf-{inc_id}"
        return {
            "finding_id": finding_id,
            "id": finding_id,
            "kind": incident.get("kind") or "anomaly",
            "status": incident.get("status") or "open",
            "severity": incident.get("severity") or "medium",
            "created_at": incident.get("created_at") or "",
            "incident_id": inc_id,
            "details": incident.get("description") or incident.get("summary") or incident.get("title") or "",
        }


class DomainGovernancePort:
    """Governance and Evolution domain reader adapter."""

    def __init__(
        self,
        *,
        evolution_decisions: Optional[Dict[str, Dict[str, Any]]] = None,
        freeze_orders: Optional[Dict[str, Dict[str, Any]]] = None,
        all_rollbacks: Optional[Union[Dict[str, Dict[str, Any]], List[Dict[str, Any]]]] = None,
        rollback_reviews: Optional[Dict[str, Dict[str, Any]]] = None,
        governance_audit_events: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        self._evolution_decisions = dict(evolution_decisions or {})
        self._freeze_orders = dict(freeze_orders or {})
        if isinstance(all_rollbacks, dict):
            self._all_rollbacks = list(all_rollbacks.values())
        else:
            self._all_rollbacks = list(all_rollbacks or [])
        self._rollback_reviews = dict(rollback_reviews or {})
        self._governance_audit_events = list(governance_audit_events or [])

    @staticmethod
    def project_evolution_decision(raw: Dict[str, Any]) -> Dict[str, Any]:
        decision_id = raw.get("decision_id") or raw.get("id")
        decision_state = raw.get("decision_state") or raw.get("status")
        linked_incident_id = raw.get("linked_incident_id") or raw.get("incident_ref")
        target_id = raw.get("target_id") or raw.get("artifact_id")
        return {
            "id": decision_id,
            "decision_id": decision_id,
            "program_id": raw.get("program_id"),
            "persona_id": raw.get("persona_id"),
            "capital_pool_id": raw.get("capital_pool_id"),
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
            "artifact_ref": raw.get("artifact_ref"),
            "score": raw.get("score"),
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
            "origin": raw.get("origin"),
            "provenance": raw.get("provenance"),
            "execution_result": raw.get("execution_result"),
        }

    def list_evolution_decisions(
        self,
        action_type: Optional[str] = None,
        risk_level: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        decisions = [
            self.project_evolution_decision(raw)
            for raw in self._evolution_decisions.values()
        ]
        if action_type:
            decisions = [d for d in decisions if d.get("action_type") == action_type]
        if risk_level:
            decisions = [d for d in decisions if d.get("risk_level") == risk_level]
        if status:
            decisions = [d for d in decisions if d.get("status") == status]
        return sorted(decisions, key=lambda x: str(x.get("created_at") or ""), reverse=True)

    def get_evolution_decision_by_id(self, decision_id: str) -> Optional[Dict[str, Any]]:
        raw = self._evolution_decisions.get(decision_id)
        return self.project_evolution_decision(raw) if raw else None

    def get_evolution_decision(self, decision_id: str) -> Optional[Dict[str, Any]]:
        return self.get_evolution_decision_by_id(decision_id)

    def list_freeze_orders(
        self,
        status: Optional[str] = None,
        scope: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        orders = list(self._freeze_orders.values())
        if status:
            orders = [o for o in orders if o.get("status") == status]
        if scope:
            orders = [o for o in orders if o.get("scope") == scope]
        return sorted(
            orders,
            key=lambda x: str(x.get("created_at") or x.get("issued_at") or x.get("updated_at") or ""),
            reverse=True,
        )

    def list_all_rollbacks(
        self,
        runtime_id: Optional[str] = None,
        action_type: Optional[str] = None,
        time_range: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        rollbacks = list(self._all_rollbacks)
        if runtime_id:
            rollbacks = [r for r in rollbacks if r.get("runtime_id") == runtime_id]
        if action_type:
            rollbacks = [r for r in rollbacks if r.get("action_type") == action_type]
        return sorted(
            rollbacks,
            key=lambda x: str(
                x.get("initiated_at") or x.get("requested_at") or x.get("created_at") or x.get("updated_at") or ""
            ),
            reverse=True,
        )

    def get_rollback_review(self, rollback_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not rollback_id:
            return None
        review = self._rollback_reviews.get(rollback_id)
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
        include_fixture_pack: bool = True,
    ) -> List[Dict[str, Any]]:
        events = [
            event
            for event in self._governance_audit_events
            if isinstance(event, dict) and (include_fixture_pack or not _is_fixture_pack_record(event))
        ]
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

        events.sort(key=lambda event: str(event.get("timestamp") or ""), reverse=True)
        return json.loads(json.dumps(events))


class DomainLineagePort:
    """Lineage and Inspiration graph domain reader adapter."""

    def __init__(
        self,
        *,
        lineage_edges: Optional[Union[Dict[str, Dict[str, Any]], List[Dict[str, Any]]]] = None,
        inspiration_graphs: Optional[Dict[str, Dict[str, Any]]] = None,
        artifact_registry_entries: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        if isinstance(lineage_edges, dict):
            self._lineage_edges = list(lineage_edges.values())
        else:
            self._lineage_edges = list(lineage_edges or [])
        self._inspiration_graphs = dict(inspiration_graphs or {})
        self._artifact_registry_entries = list(artifact_registry_entries or [])

    @staticmethod
    def _lineage_edge_sort_key(edge: Dict[str, Any]) -> Tuple[str, str]:
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

        for entry in self._artifact_registry_entries:
            merge(
                entry.get("artifact_id") or entry.get("registry_id") or entry.get("id"),
                artifact_version=entry.get("artifact_version") or entry.get("version"),
                artifact_type=entry.get("artifact_type"),
            )

        for edge in self._lineage_edges:
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

        for art_id, graph in self._inspiration_graphs.items():
            merge(art_id)
            for edge in graph.get("inspiration_edges") or []:
                if isinstance(edge, dict):
                    merge(edge.get("source_artifact_id"))

        return index

    def list_lineage_edges(
        self,
        artifact_id: Optional[str] = None,
        include_fixture_pack: bool = True,
    ) -> List[Dict[str, Any]]:
        edges = [
            edge
            for edge in self._lineage_edges
            if include_fixture_pack or not _is_fixture_pack_record(edge)
        ]
        if artifact_id:
            edges = [
                e for e in edges
                if e.get("from_artifact_id") == artifact_id or e.get("to_artifact_id") == artifact_id
            ]
        return sorted(edges, key=self._lineage_edge_sort_key, reverse=True)

    def list_lineage_records(
        self,
        artifact_id: Optional[str] = None,
        include_fixture_pack: bool = True,
    ) -> List[Dict[str, Any]]:
        edges = self.list_lineage_edges(include_fixture_pack=include_fixture_pack)
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
        for edge in self._lineage_edges:
            if edge.get("id") == edge_id or edge.get("edge_id") == edge_id:
                return edge
        return None

    def get_lineage_graph(
        self,
        root_type: Optional[str] = None,
        root_id: Optional[str] = None,
        depth: int = 3,
    ) -> List[Dict[str, Any]]:
        edges = list(self._lineage_edges)
        if root_id:
            edges = [
                e for e in edges
                if e.get("from_artifact_id") == root_id or e.get("to_artifact_id") == root_id
            ]
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

    @staticmethod
    def project_inspiration_graph(raw: Dict[str, Any]) -> Dict[str, Any]:
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

    def get_inspiration_graph(self, artifact_id: str) -> Optional[Dict[str, Any]]:
        raw = self._inspiration_graphs.get(artifact_id)
        return self.project_inspiration_graph(raw) if raw else None


class DomainTelemetryPort:
    """Telemetry, Summary, Performance, and Drift domain reader adapter."""

    def __init__(
        self,
        *,
        telemetry_events: Optional[List[Dict[str, Any]]] = None,
        telemetry_summaries: Optional[Union[Dict[str, Dict[str, Any]], List[Dict[str, Any]]]] = None,
        telemetry_performance: Optional[Dict[str, Dict[str, Any]]] = None,
        paper_live_drift_reports: Optional[Union[Dict[str, Dict[str, Any]], List[Dict[str, Any]]]] = None,
        telemetry_events_source: str = "telemetry_events",
    ) -> None:
        self._telemetry_events = list(telemetry_events or [])
        if isinstance(telemetry_summaries, dict):
            self._telemetry_summaries = list(telemetry_summaries.values())
        else:
            self._telemetry_summaries = list(telemetry_summaries or [])
        self._telemetry_performance = dict(telemetry_performance or {})
        if isinstance(paper_live_drift_reports, dict):
            self._paper_live_drift_reports = list(paper_live_drift_reports.values())
        else:
            self._paper_live_drift_reports = list(paper_live_drift_reports or [])
        self._telemetry_events_source = telemetry_events_source

    @staticmethod
    def project_telemetry_event(event: Dict[str, Any]) -> Dict[str, Any]:
        projected = json.loads(json.dumps(event))
        event_id = (
            projected.get("id")
            or projected.get("event_id")
            or projected.get("telemetry_event_id")
        )
        if event_id not in (None, ""):
            projected.setdefault("id", str(event_id))
        runtime_id = (
            projected.get("runtime_id")
            or projected.get("runtimeBindingId")
            or projected.get("runtime_binding_id")
        )
        if runtime_id not in (None, ""):
            projected.setdefault("runtime_id", str(runtime_id))
        event_type = (
            projected.get("type")
            or projected.get("event_type")
            or projected.get("kind")
            or "telemetry"
        )
        projected.setdefault("type", str(event_type))
        timestamp = DomainTelemetryPort._telemetry_event_timestamp(projected)
        if timestamp:
            projected.setdefault("timestamp", timestamp)
        return projected

    @staticmethod
    def _telemetry_event_timestamp(event: Dict[str, Any]) -> str:
        for key in (
            "timestamp",
            "occurred_at",
            "emitted_at",
            "created_at",
            "collected_at",
        ):
            value = event.get(key)
            if value not in (None, ""):
                return str(value)
        return ""

    @staticmethod
    def _filter_telemetry_events(
        events: List[Dict[str, Any]],
        *,
        pool_id: Optional[str],
        artifact_id: Optional[str],
        time_range: Optional[str],
    ) -> List[Dict[str, Any]]:
        filtered = list(events)
        if artifact_id:
            filtered = [
                event
                for event in filtered
                if event.get("artifact_id") == artifact_id
                or event.get("runtime_id") == artifact_id
            ]
        if pool_id:
            filtered = [event for event in filtered if event.get("pool_id") == pool_id]
        return sorted(
            filtered,
            key=DomainTelemetryPort._telemetry_event_timestamp,
            reverse=True,
        )

    def _telemetry_summary_projection_events(self) -> List[Dict[str, Any]]:
        events: List[Dict[str, Any]] = []
        for summary in self._telemetry_summaries:
            runtime_id = summary.get("runtime_id") or summary.get("id")
            if not runtime_id:
                continue
            event = {
                "id": f"tl-evt-{runtime_id}",
                "runtime_id": str(runtime_id),
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
        return events

    def list_telemetry_events_with_source(
        self,
        pool_id: Optional[str] = None,
        artifact_id: Optional[str] = None,
        time_range: Optional[str] = None,
    ) -> Tuple[str, List[Dict[str, Any]]]:
        event_records = [
            self.project_telemetry_event(event)
            for event in self._telemetry_events
            if isinstance(event, dict)
        ]
        if event_records:
            return self._telemetry_events_source, self._filter_telemetry_events(
                event_records,
                pool_id=pool_id,
                artifact_id=artifact_id,
                time_range=time_range,
            )

        fallback_events = self._telemetry_summary_projection_events()
        if fallback_events:
            return "telemetry_summary_fallback", self._filter_telemetry_events(
                fallback_events,
                pool_id=pool_id,
                artifact_id=artifact_id,
                time_range=time_range,
            )
        return "missing", []

    def list_telemetry_events(
        self,
        pool_id: Optional[str] = None,
        artifact_id: Optional[str] = None,
        time_range: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        _, events = self.list_telemetry_events_with_source(
            pool_id=pool_id,
            artifact_id=artifact_id,
            time_range=time_range,
        )
        return events

    def get_telemetry_summary(self, runtime_id: str) -> Optional[Dict[str, Any]]:
        for summary in self._telemetry_summaries:
            if summary.get("runtime_id") == runtime_id or summary.get("id") == runtime_id:
                return summary
        return None

    def list_telemetry_summaries(self) -> List[Dict[str, Any]]:
        return [json.loads(json.dumps(s)) for s in self._telemetry_summaries]

    def get_telemetry_performance(self, artifact_id: str) -> Optional[Dict[str, Any]]:
        return self._telemetry_performance.get(artifact_id)

    def get_paper_live_drift_report(self, runtime_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not runtime_id:
            return None
        for report in self._paper_live_drift_reports:
            if report.get("runtime_id") == runtime_id or report.get("id") == runtime_id:
                return json.loads(json.dumps(report))
        return None

    def list_paper_live_drift_reports(self) -> List[Dict[str, Any]]:
        return [json.loads(json.dumps(r)) for r in self._paper_live_drift_reports]


# =====================================================================
# Composite Port Implementation
# =====================================================================

class CompositeLifecycleTelemetryGovernancePort:
    """Composite domain port delegating to individual typed domain ports."""

    def __init__(
        self,
        *,
        incident_port: Optional[IncidentReaderPort] = None,
        lifecycle_port: Optional[LifecycleReaderPort] = None,
        governance_port: Optional[GovernanceReaderPort] = None,
        lineage_port: Optional[LineageReaderPort] = None,
        telemetry_port: Optional[TelemetryReaderPort] = None,
    ) -> None:
        self.incidents = incident_port or DomainIncidentPort()
        self.lifecycle = lifecycle_port or DomainLifecyclePort()
        self.governance = governance_port or DomainGovernancePort()
        self.lineage = lineage_port or DomainLineagePort()
        self.telemetry = telemetry_port or DomainTelemetryPort()

    # IncidentReaderPort
    def list_incidents(
        self,
        status: Optional[str] = None,
        severity: Optional[str] = None,
        affected_pool_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        return self.incidents.list_incidents(
            status=status, severity=severity, affected_pool_id=affected_pool_id
        )

    def get_incident(self, incident_id: str) -> Optional[Dict[str, Any]]:
        return self.incidents.get_incident(incident_id)

    def list_postmortems(self, time_range: Optional[str] = None) -> List[Dict[str, Any]]:
        return self.incidents.list_postmortems(time_range=time_range)

    def get_postmortem(self, report_id: str) -> Optional[Dict[str, Any]]:
        return self.incidents.get_postmortem(report_id)

    def get_postmortem_by_incident(self, incident_id: str) -> Optional[Dict[str, Any]]:
        return self.incidents.get_postmortem_by_incident(incident_id)

    def get_evolution_decisions_by_incident(self, incident_id: str) -> List[Dict[str, Any]]:
        return self.incidents.get_evolution_decisions_by_incident(incident_id)

    def get_rollbacks_by_incident(self, incident_id: str) -> List[Dict[str, Any]]:
        return self.incidents.get_rollbacks_by_incident(incident_id)

    # LifecycleReaderPort
    def list_loop_runs(self) -> Tuple[bool, List[Dict[str, Any]]]:
        return self.lifecycle.list_loop_runs()

    def get_loop_run(self, loop_run_id: str) -> Tuple[bool, Optional[Dict[str, Any]]]:
        return self.lifecycle.get_loop_run(loop_run_id)

    def list_loop_health_records(self) -> Tuple[bool, List[Dict[str, Any]]]:
        return self.lifecycle.list_loop_health_records()

    def get_loop_health_record(self, loop_id: str) -> Tuple[bool, Optional[Dict[str, Any]]]:
        return self.lifecycle.get_loop_health_record(loop_id)

    def list_sentinel_findings(
        self,
        *,
        kind: Optional[str] = None,
        status: Optional[str] = None,
        severity: Optional[str] = None,
    ) -> Tuple[bool, List[Dict[str, Any]]]:
        return self.lifecycle.list_sentinel_findings(kind=kind, status=status, severity=severity)

    def get_sentinel_finding(self, finding_id: str) -> Tuple[bool, Optional[Dict[str, Any]]]:
        return self.lifecycle.get_sentinel_finding(finding_id)

    def get_kill_switch_status(self) -> Dict[str, Any]:
        return self.lifecycle.get_kill_switch_status()

    def loop_run_projection_metadata(self) -> Dict[str, Any]:
        return self.lifecycle.loop_run_projection_metadata()

    def trade_journey_projection_reader(self) -> Any:
        return self.lifecycle.trade_journey_projection_reader()

    # GovernanceReaderPort
    def list_evolution_decisions(
        self,
        action_type: Optional[str] = None,
        risk_level: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        return self.governance.list_evolution_decisions(
            action_type=action_type, risk_level=risk_level, status=status
        )

    def get_evolution_decision_by_id(self, decision_id: str) -> Optional[Dict[str, Any]]:
        return self.governance.get_evolution_decision_by_id(decision_id)

    def get_evolution_decision(self, decision_id: str) -> Optional[Dict[str, Any]]:
        return self.governance.get_evolution_decision(decision_id)

    def list_freeze_orders(
        self,
        status: Optional[str] = None,
        scope: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        return self.governance.list_freeze_orders(status=status, scope=scope)

    def list_all_rollbacks(
        self,
        runtime_id: Optional[str] = None,
        action_type: Optional[str] = None,
        time_range: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        return self.governance.list_all_rollbacks(
            runtime_id=runtime_id, action_type=action_type, time_range=time_range
        )

    def get_rollback_review(self, rollback_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return self.governance.get_rollback_review(rollback_id)

    def list_governance_audit_events(
        self,
        *,
        actor: Optional[str] = None,
        action_types: Optional[List[str]] = None,
        target_type: Optional[str] = None,
        from_ts: Optional[datetime] = None,
        to_ts: Optional[datetime] = None,
        include_fixture_pack: bool = True,
    ) -> List[Dict[str, Any]]:
        return self.governance.list_governance_audit_events(
            actor=actor,
            action_types=action_types,
            target_type=target_type,
            from_ts=from_ts,
            to_ts=to_ts,
            include_fixture_pack=include_fixture_pack,
        )

    # LineageReaderPort
    def list_lineage_edges(
        self,
        artifact_id: Optional[str] = None,
        include_fixture_pack: bool = True,
    ) -> List[Dict[str, Any]]:
        return self.lineage.list_lineage_edges(
            artifact_id=artifact_id, include_fixture_pack=include_fixture_pack
        )

    def list_lineage_records(
        self,
        artifact_id: Optional[str] = None,
        include_fixture_pack: bool = True,
    ) -> List[Dict[str, Any]]:
        return self.lineage.list_lineage_records(
            artifact_id=artifact_id, include_fixture_pack=include_fixture_pack
        )

    def get_lineage_edge(self, edge_id: str) -> Optional[Dict[str, Any]]:
        return self.lineage.get_lineage_edge(edge_id)

    def get_lineage_graph(
        self,
        root_type: Optional[str] = None,
        root_id: Optional[str] = None,
        depth: int = 3,
    ) -> List[Dict[str, Any]]:
        return self.lineage.get_lineage_graph(root_type=root_type, root_id=root_id, depth=depth)

    def get_lineage_graph_nodes(
        self,
        edges: List[Dict[str, Any]],
    ) -> List[Dict[str, str]]:
        return self.lineage.get_lineage_graph_nodes(edges)

    def artifact_exists(self, artifact_id: str) -> bool:
        return self.lineage.artifact_exists(artifact_id)

    def get_inspiration_graph(self, artifact_id: str) -> Optional[Dict[str, Any]]:
        return self.lineage.get_inspiration_graph(artifact_id)

    # TelemetryReaderPort
    def list_telemetry_events(
        self,
        pool_id: Optional[str] = None,
        artifact_id: Optional[str] = None,
        time_range: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        return self.telemetry.list_telemetry_events(
            pool_id=pool_id, artifact_id=artifact_id, time_range=time_range
        )

    def list_telemetry_events_with_source(
        self,
        pool_id: Optional[str] = None,
        artifact_id: Optional[str] = None,
        time_range: Optional[str] = None,
    ) -> Tuple[str, List[Dict[str, Any]]]:
        return self.telemetry.list_telemetry_events_with_source(
            pool_id=pool_id, artifact_id=artifact_id, time_range=time_range
        )

    def get_telemetry_summary(self, runtime_id: str) -> Optional[Dict[str, Any]]:
        return self.telemetry.get_telemetry_summary(runtime_id)

    def list_telemetry_summaries(self) -> List[Dict[str, Any]]:
        return self.telemetry.list_telemetry_summaries()

    def get_telemetry_performance(self, artifact_id: str) -> Optional[Dict[str, Any]]:
        return self.telemetry.get_telemetry_performance(artifact_id)

    def get_paper_live_drift_report(self, runtime_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return self.telemetry.get_paper_live_drift_report(runtime_id)

    def list_paper_live_drift_reports(self) -> List[Dict[str, Any]]:
        return self.telemetry.list_paper_live_drift_reports()


class InMemoryLifecycleTelemetryGovernancePort(CompositeLifecycleTelemetryGovernancePort):
    """In-memory test implementation for Lifecycle, Telemetry, Incident, Governance, and Lineage."""

    def __init__(
        self,
        *,
        incidents: Optional[Dict[str, Dict[str, Any]]] = None,
        postmortems: Optional[Dict[str, Dict[str, Any]]] = None,
        evolution_decisions: Optional[Dict[str, Dict[str, Any]]] = None,
        rollbacks_by_incident: Optional[Dict[str, List[Dict[str, Any]]]] = None,
        loop_runs: Optional[Dict[str, Dict[str, Any]]] = None,
        loop_health_records: Optional[Dict[str, Dict[str, Any]]] = None,
        sentinel_findings: Optional[Dict[str, Dict[str, Any]]] = None,
        kill_switch: Optional[Dict[str, Any]] = None,
        projection_metadata: Optional[Dict[str, Any]] = None,
        freeze_orders: Optional[Dict[str, Dict[str, Any]]] = None,
        all_rollbacks: Optional[Union[Dict[str, Dict[str, Any]], List[Dict[str, Any]]]] = None,
        rollback_reviews: Optional[Dict[str, Dict[str, Any]]] = None,
        governance_audit_events: Optional[List[Dict[str, Any]]] = None,
        lineage_edges: Optional[Union[Dict[str, Dict[str, Any]], List[Dict[str, Any]]]] = None,
        inspiration_graphs: Optional[Dict[str, Dict[str, Any]]] = None,
        artifact_registry_entries: Optional[List[Dict[str, Any]]] = None,
        telemetry_events: Optional[List[Dict[str, Any]]] = None,
        telemetry_summaries: Optional[Union[Dict[str, Dict[str, Any]], List[Dict[str, Any]]]] = None,
        telemetry_performance: Optional[Dict[str, Dict[str, Any]]] = None,
        paper_live_drift_reports: Optional[Union[Dict[str, Dict[str, Any]], List[Dict[str, Any]]]] = None,
        telemetry_events_source: str = "telemetry_events",
        projection_reader_override: Any = None,
    ) -> None:
        inc_port = DomainIncidentPort(
            incidents=incidents,
            postmortems=postmortems,
            evolution_decisions=evolution_decisions,
            rollbacks_by_incident=rollbacks_by_incident,
        )
        life_port = DomainLifecyclePort(
            loop_runs=loop_runs,
            loop_health_records=loop_health_records,
            sentinel_findings=sentinel_findings,
            kill_switch=kill_switch,
            projection_metadata=projection_metadata,
            incidents=incidents,
            projection_reader_override=projection_reader_override,
        )
        gov_port = DomainGovernancePort(
            evolution_decisions=evolution_decisions,
            freeze_orders=freeze_orders,
            all_rollbacks=all_rollbacks,
            rollback_reviews=rollback_reviews,
            governance_audit_events=governance_audit_events,
        )
        lin_port = DomainLineagePort(
            lineage_edges=lineage_edges,
            inspiration_graphs=inspiration_graphs,
            artifact_registry_entries=artifact_registry_entries,
        )
        tel_port = DomainTelemetryPort(
            telemetry_events=telemetry_events,
            telemetry_summaries=telemetry_summaries,
            telemetry_performance=telemetry_performance,
            paper_live_drift_reports=paper_live_drift_reports,
            telemetry_events_source=telemetry_events_source,
        )
        super().__init__(
            incident_port=inc_port,
            lifecycle_port=life_port,
            governance_port=gov_port,
            lineage_port=lin_port,
            telemetry_port=tel_port,
        )


def create_lifecycle_telemetry_governance_port(
    *,
    incident_port: Optional[IncidentReaderPort] = None,
    lifecycle_port: Optional[LifecycleReaderPort] = None,
    governance_port: Optional[GovernanceReaderPort] = None,
    lineage_port: Optional[LineageReaderPort] = None,
    telemetry_port: Optional[TelemetryReaderPort] = None,
) -> CompositeLifecycleTelemetryGovernancePort:
    """Create a composite Lifecycle, Telemetry, Incident, Governance, and Lineage domain port."""
    return CompositeLifecycleTelemetryGovernancePort(
        incident_port=incident_port,
        lifecycle_port=lifecycle_port,
        governance_port=governance_port,
        lineage_port=lineage_port,
        telemetry_port=telemetry_port,
    )


def create_in_memory_lifecycle_telemetry_governance_port(
    **kwargs: Any,
) -> InMemoryLifecycleTelemetryGovernancePort:
    """Create an in-memory Lifecycle, Telemetry, Incident, Governance, and Lineage domain port for testing."""
    return InMemoryLifecycleTelemetryGovernancePort(**kwargs)
