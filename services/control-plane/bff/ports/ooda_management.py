"""Domain port for OODA loop packets, interventions, conflict logs, and review queues.

This module provides typed domain ports and explicit Management compositions for:
- OODA loop packets and stage transitions backed by existing OODA stores
- V5 intervention fixture and runtime reads
- Synthesis conflict resolution logs
- Explicit Management review queue and approval queue compositions
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Set, Tuple, Union


def _utc_now_rfc3339() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_rfc3339(val: Any) -> Optional[datetime]:
    if not val or not isinstance(val, str):
        return None
    try:
        clean = val.replace("Z", "+00:00")
        return datetime.fromisoformat(clean)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# OODA Reference Extraction and Matching Helpers
# ---------------------------------------------------------------------------

def _ooda_packet_id(packet: Mapping[str, Any]) -> str:
    return str(packet.get("packet_id") or packet.get("id") or "").strip()


def _add_ooda_ref_value(target: Set[str], value: Any) -> None:
    if value in (None, ""):
        return
    if isinstance(value, (str, int, float)):
        text = str(value).strip()
        if text:
            target.add(text)
        return
    if isinstance(value, list):
        for item in value:
            _add_ooda_ref_value(target, item)
        return
    if isinstance(value, dict):
        for field_name in ("id", "ref_id", "object_id", "entity_id", "strategy_id", "runtime_id", "program_id"):
            raw = value.get(field_name)
            if raw not in (None, ""):
                target.add(str(raw).strip())


def _collect_ooda_ref_values(
    value: Any,
    *,
    field_aliases: Set[str],
    type_tokens: Set[str],
) -> Set[str]:
    refs: Set[str] = set()

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            raw_type = str(
                node.get("type")
                or node.get("object_type")
                or node.get("entity_type")
                or node.get("ref_type")
                or ""
            ).lower()
            if raw_type and any(token in raw_type for token in type_tokens):
                _add_ooda_ref_value(refs, node)
            for key, child in node.items():
                normalized_key = str(key).replace("_", "").lower()
                if normalized_key in field_aliases:
                    _add_ooda_ref_value(refs, child)
                visit(child)
            return
        if isinstance(node, list):
            for child in node:
                visit(child)

    visit(value)
    return refs


def _ooda_packet_matches_ref(packet: Mapping[str, Any], ref_id: str, ref_type: str) -> bool:
    clean_ref = str(ref_id or "").strip()
    if not clean_ref:
        return False
    aliases_by_type = {
        "strategy": {
            "strategyid",
            "strategyids",
            "linkedstrategyid",
            "linkedstrategyids",
            "strategyspecid",
            "strategyspecids",
        },
        "runtime": {
            "runtimeid",
            "runtimeids",
            "runtimebindingid",
            "runtimebindingids",
            "bindingid",
            "bindingids",
        },
        "evolution_program": {
            "evolutionprogramid",
            "evolutionprogramids",
            "programid",
            "programids",
        },
    }
    type_tokens_by_type = {
        "strategy": {"strategy"},
        "runtime": {"runtime", "runtimebinding"},
        "evolution_program": {"evolutionprogram"},
    }
    if ref_type not in aliases_by_type:
        return False
    refs = _collect_ooda_ref_values(
        packet,
        field_aliases=aliases_by_type[ref_type],
        type_tokens=type_tokens_by_type[ref_type],
    )
    return clean_ref in refs


# ---------------------------------------------------------------------------
# OODA Packets Port
# ---------------------------------------------------------------------------

class OodaPacketsPort:
    """Port for listing and retrieving OODA loop packets."""

    def __init__(
        self,
        *,
        store: Optional[Any] = None,
        records_provider: Optional[Callable[[], List[Dict[str, Any]]]] = None,
    ) -> None:
        self._store = store
        self._records_provider = records_provider

    def _get_raw_records(self) -> Tuple[str, List[Dict[str, Any]]]:
        if self._store is not None:
            try:
                # 1. OodaLoopStore interface
                if hasattr(self._store, "list") and callable(self._store.list):
                    raw_packets = self._store.list()
                    records = [
                        p.to_dict() if hasattr(p, "to_dict") else dict(p)
                        for p in raw_packets
                    ]
                    return "store", records
                # 2. OodaJsonlAppendStore interface
                if hasattr(self._store, "list_packets") and callable(self._store.list_packets):
                    raw_packets = self._store.list_packets()
                    records = [
                        p.to_dict() if hasattr(p, "to_dict") else dict(p)
                        for p in raw_packets
                    ]
                    return "store", records
            except Exception:
                return "unavailable", []

        if self._records_provider is not None:
            try:
                records = self._records_provider()
                return "service", [dict(r) for r in (records or [])]
            except Exception:
                return "unavailable", []

        return "missing", []

    def get_surface_status(self) -> Dict[str, Any]:
        source, records = self._get_raw_records()
        if source in ("missing", "unavailable"):
            return {
                "status": "unavailable",
                "source": source,
                "message": "OODA loop packet store is unavailable or unconfigured.",
            }
        return {
            "status": "ok" if records else "degraded",
            "source": source,
            "message": None if records else "OODA loop packet store is empty.",
        }

    def list_ooda_packets(
        self,
        *,
        status: Optional[str] = None,
        stage: Optional[str] = None,
        strategy_id: Optional[str] = None,
        runtime_id: Optional[str] = None,
        evolution_program_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        _, raw_records = self._get_raw_records()
        items = [
            json.loads(json.dumps(packet))
            for packet in raw_records
            if _ooda_packet_id(packet)
        ]

        if status:
            requested = {item.strip().lower() for item in status.split(",") if item.strip()}
            items = [
                packet
                for packet in items
                if str(packet.get("status") or packet.get("state") or "").lower() in requested
            ]
        if stage:
            requested_stages = {item.strip().lower() for item in stage.split(",") if item.strip()}
            items = [
                packet
                for packet in items
                if str(packet.get("stage") or packet.get("current_stage") or "").lower() in requested_stages
            ]
        if strategy_id:
            items = [
                packet
                for packet in items
                if _ooda_packet_matches_ref(packet, strategy_id, "strategy")
            ]
        if runtime_id:
            items = [
                packet
                for packet in items
                if _ooda_packet_matches_ref(packet, runtime_id, "runtime")
            ]
        if evolution_program_id:
            items = [
                packet
                for packet in items
                if _ooda_packet_matches_ref(packet, evolution_program_id, "evolution_program")
            ]

        items.sort(
            key=lambda packet: (
                (
                    _parse_rfc3339(
                        packet.get("updated_at")
                        or packet.get("closed_at")
                        or packet.get("created_at")
                        or packet.get("started_at")
                    )
                    or datetime.min
                ).replace(tzinfo=None)
            ),
            reverse=True,
        )
        return items

    def get_ooda_packet(self, packet_id: Optional[str]) -> Optional[Dict[str, Any]]:
        clean_id = str(packet_id or "").strip()
        if not clean_id:
            return None
        for packet in self.list_ooda_packets():
            if _ooda_packet_id(packet) == clean_id:
                return json.loads(json.dumps(packet))
        return None

    def list_ooda_packets_for_strategy(self, strategy_id: str) -> List[Dict[str, Any]]:
        return self.list_ooda_packets(strategy_id=strategy_id)

    def list_ooda_packets_for_runtime(self, runtime_id: str) -> List[Dict[str, Any]]:
        return self.list_ooda_packets(runtime_id=runtime_id)

    def list_ooda_packets_for_evolution_program(self, program_id: str) -> List[Dict[str, Any]]:
        return self.list_ooda_packets(evolution_program_id=program_id)


# ---------------------------------------------------------------------------
# Interventions Port
# ---------------------------------------------------------------------------

class InterventionsPort:
    """Port for listing and retrieving V5 interventions."""

    def __init__(
        self,
        *,
        records_provider: Optional[Callable[[], List[Dict[str, Any]]]] = None,
        store: Optional[Any] = None,
    ) -> None:
        self._records_provider = records_provider
        self._store = store

    def _get_raw_records(self) -> Tuple[str, List[Dict[str, Any]]]:
        if self._records_provider is not None:
            try:
                records = self._records_provider()
                return "store", [dict(r) for r in (records or [])]
            except Exception:
                return "unavailable", []
        if self._store is not None:
            try:
                if hasattr(self._store, "list_v5_interventions"):
                    return "store", self._store.list_v5_interventions()
                if hasattr(self._store, "list_records"):
                    avail, recs = self._store.list_records("v5_interventions")
                    if avail:
                        return "store", list(recs or [])
            except Exception:
                return "unavailable", []
        return "missing", []

    def get_surface_status(self) -> Dict[str, Any]:
        source, records = self._get_raw_records()
        if source in ("missing", "unavailable"):
            return {
                "status": "unavailable",
                "source": source,
                "message": "Intervention store is unavailable.",
            }
        return {
            "status": "ok" if records else "degraded",
            "source": source,
            "message": None if records else "Intervention store is empty.",
        }

    def list_interventions(
        self,
        *,
        status: Optional[str] = None,
        kind: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        _, raw_items = self._get_raw_records()
        items = [dict(item) for item in raw_items if isinstance(item, dict)]
        if status:
            items = [
                item
                for item in items
                if str(item.get("status") or "").strip().lower() == str(status).strip().lower()
            ]
        if kind:
            items = [
                item
                for item in items
                if str(item.get("kind") or "").strip().lower() == str(kind).strip().lower()
            ]
        items.sort(
            key=lambda item: (
                (_parse_rfc3339(item.get("triggered_at")) or datetime.min).replace(tzinfo=None)
            ),
            reverse=True,
        )
        return json.loads(json.dumps(items))

    def get_intervention(self, intervention_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not intervention_id:
            return None
        clean_id = str(intervention_id).strip()
        for item in self.list_interventions():
            found_id = str(item.get("intervention_id") or item.get("id") or "").strip()
            if found_id == clean_id:
                return json.loads(json.dumps(item))
        return None


# ---------------------------------------------------------------------------
# Synthesis Conflict Logs Port
# ---------------------------------------------------------------------------

class SynthesisConflictLogsPort:
    """Port for listing and retrieving synthesis conflict resolution logs."""

    def __init__(
        self,
        *,
        records_provider: Optional[Callable[[], List[Dict[str, Any]]]] = None,
        store: Optional[Any] = None,
    ) -> None:
        self._records_provider = records_provider
        self._store = store

    @staticmethod
    def _log_id(log: Mapping[str, Any]) -> str:
        return str(log.get("log_id") or log.get("id") or log.get("conflict_resolution_log_id") or "").strip()

    @staticmethod
    def _text_matches(value: Any, requested: Set[str]) -> bool:
        if value in (None, ""):
            return False
        if isinstance(value, list):
            return any(SynthesisConflictLogsPort._text_matches(item, requested) for item in value)
        return str(value).strip() in requested

    @classmethod
    def _matches_proposal(cls, log: Mapping[str, Any], proposal_id: str) -> bool:
        clean_id = str(proposal_id or "").strip()
        if not clean_id:
            return True
        requested = {clean_id}
        if cls._text_matches(log.get("proposal_ids"), requested):
            return True
        if clean_id in {str(key) for key in (log.get("weighting_inputs") or {}).keys()}:
            return True
        if clean_id in {str(key) for key in (log.get("weighting_outputs") or {}).keys()}:
            return True
        for veto in log.get("vetoed_proposals") or []:
            if isinstance(veto, dict) and str(veto.get("proposal_id") or "").strip() == clean_id:
                return True
        return False

    def _get_raw_records(self) -> Tuple[str, List[Dict[str, Any]]]:
        if self._records_provider is not None:
            try:
                records = self._records_provider()
                return "store", [dict(r) for r in (records or [])]
            except Exception:
                return "unavailable", []
        if self._store is not None:
            try:
                if hasattr(self._store, "list_synthesis_conflict_logs"):
                    return "store", self._store.list_synthesis_conflict_logs()
                if hasattr(self._store, "list_records"):
                    avail, recs = self._store.list_records("synthesis_conflict_logs")
                    if avail:
                        return "store", list(recs or [])
            except Exception:
                return "unavailable", []
        return "missing", []

    def list_synthesis_conflict_logs(
        self,
        *,
        capital_pool_id: Optional[str] = None,
        scope_ref: Optional[str] = None,
        proposal_id: Optional[str] = None,
        sponsor_persona_id: Optional[str] = None,
        synthesis_method: Optional[str] = None,
        committee_ref: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        _, raw_items = self._get_raw_records()
        items = [
            json.loads(json.dumps(log))
            for log in raw_items
            if self._log_id(log)
        ]
        if capital_pool_id:
            requested = {item.strip() for item in capital_pool_id.split(",") if item.strip()}
            items = [log for log in items if str(log.get("capital_pool_id") or "").strip() in requested]
        if scope_ref:
            requested = {item.strip() for item in scope_ref.split(",") if item.strip()}
            items = [log for log in items if str(log.get("scope_ref") or "").strip() in requested]
        if sponsor_persona_id:
            requested = {item.strip() for item in sponsor_persona_id.split(",") if item.strip()}
            items = [log for log in items if str(log.get("sponsor_persona_id") or "").strip() in requested]
        if synthesis_method:
            requested = {item.strip() for item in synthesis_method.split(",") if item.strip()}
            items = [log for log in items if str(log.get("synthesis_method") or "").strip() in requested]
        if committee_ref:
            requested = {item.strip() for item in committee_ref.split(",") if item.strip()}
            items = [log for log in items if str(log.get("committee_ref") or "").strip() in requested]
        if proposal_id:
            items = [log for log in items if self._matches_proposal(log, proposal_id)]
        items.sort(
            key=lambda log: (
                (
                    _parse_rfc3339(
                        log.get("timestamp")
                        or log.get("created_at")
                        or log.get("recorded_at")
                        or log.get("updated_at")
                    )
                    or datetime.min
                ).replace(tzinfo=None)
            ),
            reverse=True,
        )
        return items

    def get_synthesis_conflict_log(self, log_id: Optional[str]) -> Optional[Dict[str, Any]]:
        clean_id = str(log_id or "").strip()
        if not clean_id:
            return None
        for log in self.list_synthesis_conflict_logs():
            if self._log_id(log) == clean_id:
                return json.loads(json.dumps(log))
        return None


# ---------------------------------------------------------------------------
# Management Review Queues Port (Explicit Composition)
# ---------------------------------------------------------------------------

class ManagementReviewQueuePort:
    """Port for explicit Management review and approval queue compositions."""

    REVIEWABLE_STATUSES = {"draft", "pending_review", "proposed", "under_review", "reviewed", "in_review", "pending"}
    PENDING_DECISION_STATES = {"proposed", "under_review", "reviewed", "pending", "in_review"}
    TERMINAL_DECISION_OUTCOMES = {"approved", "approved_with_conditions", "rejected"}

    def __init__(
        self,
        *,
        deployment_plans_reader: Optional[Callable[[], List[Dict[str, Any]]]] = None,
        approval_decisions_reader: Optional[Callable[[], List[Dict[str, Any]]]] = None,
        evolution_decisions_reader: Optional[Callable[[], List[Dict[str, Any]]]] = None,
        deployment_diffs_reader: Optional[Callable[[str], Optional[Dict[str, Any]]]] = None,
    ) -> None:
        self._deployment_plans_reader = deployment_plans_reader
        self._approval_decisions_reader = approval_decisions_reader
        self._evolution_decisions_reader = evolution_decisions_reader
        self._deployment_diffs_reader = deployment_diffs_reader

    @classmethod
    def derive_can_review_deployment_plan(
        cls,
        plan: Mapping[str, Any],
        decision: Optional[Mapping[str, Any]],
    ) -> bool:
        status = str(plan.get("status") or "").strip().lower()
        if status in ("active", "cancelled", "superseded", "failed"):
            return False
        if decision:
            decision_state = str(decision.get("state") or decision.get("decision_state") or "").strip().lower()
            outcome = str(decision.get("outcome") or decision.get("decision") or "").strip().lower()
            if outcome in cls.TERMINAL_DECISION_OUTCOMES:
                return False
            if decision_state in ("under_review", "reviewed", "in_review", "proposed", "pending"):
                return True
        return status in cls.REVIEWABLE_STATUSES

    @classmethod
    def derive_can_promote_to_paper(
        cls,
        plan: Mapping[str, Any],
        decision: Optional[Mapping[str, Any]],
    ) -> bool:
        target_stage = str(plan.get("target_stage") or "").strip().lower()
        status = str(plan.get("status") or "").strip().lower()
        if target_stage != "paper":
            return False
        if status == "ready_to_deploy":
            return True
        if decision:
            outcome = str(decision.get("outcome") or decision.get("decision") or "").strip().lower()
            if outcome in ("approved", "approved_with_conditions"):
                return True
        return False

    @classmethod
    def derive_allowed_actions_for_plan(
        cls,
        plan: Mapping[str, Any],
        decision: Optional[Mapping[str, Any]],
    ) -> Dict[str, bool]:
        can_review = cls.derive_can_review_deployment_plan(plan, decision)
        can_promote = cls.derive_can_promote_to_paper(plan, decision)
        return {
            "canApprove": can_review,
            "canReject": can_review,
            "canPromoteToPaper": can_promote,
        }

    @classmethod
    def derive_review_summary_for_plan(
        cls,
        plan: Mapping[str, Any],
        decision: Optional[Mapping[str, Any]],
    ) -> Dict[str, Any]:
        summary: Dict[str, Any] = {}
        if decision:
            summary["governanceOutcome"] = decision.get("outcome")
            summary["decisionState"] = decision.get("state") or decision.get("decision_state")
            summary["decidedAt"] = decision.get("decided_at")
            summary["reviewer"] = decision.get("reviewer")
            risk_level = decision.get("risk_level")
            if risk_level:
                summary["riskSummary"] = f"Approval decision risk level: {risk_level}."
        if "riskSummary" not in summary or not summary["riskSummary"]:
            summary["riskSummary"] = plan.get("risk_summary") or "Risk summary unavailable."
        return summary

    def list_governance_review_queue_items(
        self,
        *,
        item_types: Optional[List[str]] = None,
        risk_levels: Optional[List[str]] = None,
        statuses: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        linked_approval_decision_ids: Set[str] = set()

        decisions = self._approval_decisions_reader() if self._approval_decisions_reader else []
        decisions_by_id: Dict[str, Dict[str, Any]] = {}
        for d in decisions:
            d_id = str(d.get("decision_id") or d.get("id") or "").strip()
            if d_id:
                decisions_by_id[d_id] = d

        # 1. Deployment Plans
        plans = self._deployment_plans_reader() if self._deployment_plans_reader else []
        for plan in plans:
            status = str(plan.get("status") or "").strip().lower()
            if status and status not in self.REVIEWABLE_STATUSES:
                continue
            plan_id = str(plan.get("plan_id") or plan.get("id") or "").strip()
            if not plan_id:
                continue
            dec_id = str(plan.get("approval_decision_id") or "").strip()
            decision = decisions_by_id.get(dec_id)
            decision_id = str((decision or {}).get("decision_id") or (decision or {}).get("id") or "").strip()
            if decision_id:
                linked_approval_decision_ids.add(decision_id)

            items.append(
                {
                    "item_id": f"review-{plan_id}",
                    "item_type": "DeploymentPlan",
                    "risk_level": (decision or {}).get("risk_level"),
                    "submitted_at": plan.get("submitted_at") or plan.get("created_at"),
                    "submitted_by": plan.get("created_by") or "deployment-service",
                    "governance_outcome": (decision or {}).get("outcome"),
                    "allowedActions": self.derive_allowed_actions_for_plan(plan, decision),
                    "review_summary": self.derive_review_summary_for_plan(plan, decision),
                }
            )

        # 2. Evolution Decisions
        evo_decisions = self._evolution_decisions_reader() if self._evolution_decisions_reader else []
        for decision in evo_decisions:
            status = str(decision.get("decision_state") or decision.get("status") or "").strip().lower()
            if status and status not in self.REVIEWABLE_STATUSES:
                continue
            decision_id = str(decision.get("decision_id") or decision.get("id") or "").strip()
            if not decision_id:
                continue
            items.append(
                {
                    "item_id": f"review-{decision_id}",
                    "item_type": "EvolutionDecision",
                    "risk_level": decision.get("risk_level"),
                    "submitted_at": decision.get("created_at"),
                    "submitted_by": decision.get("created_by_id") or "evolution-service",
                    "governance_outcome": decision.get("status"),
                    "allowedActions": {
                        "canApprove": status in {"reviewed", "under_review", "in_review"},
                        "canReject": status in {"reviewed", "under_review", "in_review"},
                        "canRequestRevision": status in {"proposed", "under_review", "reviewed", "pending"},
                    },
                    "review_summary": {
                        "riskSummary": decision.get("rationale") or "Evolution decision awaiting governance review.",
                    },
                }
            )

        # 3. Unlinked Approval Decisions
        for decision in decisions:
            decision_id = str(decision.get("decision_id") or decision.get("id") or "").strip()
            if not decision_id or decision_id in linked_approval_decision_ids:
                continue
            status = str(decision.get("state") or decision.get("decision_state") or "").strip().lower()
            outcome = str(decision.get("outcome") or decision.get("decision") or "").strip().lower()
            if outcome in self.TERMINAL_DECISION_OUTCOMES:
                continue
            if status and status not in self.REVIEWABLE_STATUSES:
                continue
            target_type = str(decision.get("target_type") or decision.get("decision_type") or "ApprovalDecision")
            submitted_by = decision.get("created_by") or decision.get("reviewer") or "governance-service"
            can_decide = status in {"under_review", "reviewed", "in_review"}
            items.append(
                {
                    "item_id": f"review-{decision_id}",
                    "item_type": "ApprovalDecision",
                    "risk_level": decision.get("risk_level"),
                    "status": status or "proposed",
                    "submitted_at": decision.get("submitted_at") or decision.get("created_at"),
                    "submitted_by": submitted_by,
                    "governance_outcome": outcome or status or "proposed",
                    "allowedActions": {
                        "canApprove": can_decide,
                        "canReject": can_decide,
                        "canRequestRevision": status in {"proposed", "under_review", "reviewed", "pending"},
                    },
                    "review_summary": {
                        "riskSummary": (
                            decision.get("rationale")
                            or f"{target_type} approval decision awaiting governance review."
                        ),
                        "evidence_refs": json.loads(json.dumps(decision.get("evidence_refs") or [])),
                        "linked_approval_decision_id": decision_id,
                        "target_type": target_type,
                        "target_id": decision.get("target_id"),
                        "target_version": decision.get("target_version"),
                    },
                }
            )

        # Filter items
        if item_types:
            requested_item_types = {value for value in item_types if value}
            items = [item for item in items if str(item.get("item_type") or "") in requested_item_types]
        if risk_levels:
            requested_risk_levels = {value for value in risk_levels if value}
            items = [item for item in items if str(item.get("risk_level") or "") in requested_risk_levels]
        if statuses:
            requested_statuses = {value for value in statuses if value}
            items = [item for item in items if str(item.get("status") or "") in requested_statuses]

        return [json.loads(json.dumps(item)) for item in items]

    def list_approval_queue_items(
        self,
        *,
        decision_types: Optional[List[str]] = None,
        risk_levels: Optional[List[str]] = None,
        decision_states: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        raw_decisions = self._approval_decisions_reader() if self._approval_decisions_reader else []
        items: List[Dict[str, Any]] = []

        for raw in raw_decisions:
            decision_id = str(raw.get("decision_id") or raw.get("id") or "").strip()
            if not decision_id:
                continue
            state = str(raw.get("decision_state") or raw.get("state") or "").strip().lower()
            outcome = str(raw.get("outcome") or raw.get("decision") or "").strip().lower()
            if outcome in self.TERMINAL_DECISION_OUTCOMES:
                continue
            if state and state not in self.PENDING_DECISION_STATES:
                continue
            target_type = raw.get("target_type") or raw.get("decision_type") or "ApprovalDecision"
            can_decide = state in {"under_review", "reviewed", "in_review"}
            items.append(
                {
                    "decision_id": decision_id,
                    "decision_type": target_type,
                    "risk_level": raw.get("risk_level"),
                    "submitted_at": raw.get("created_at") or raw.get("submitted_at"),
                    "submitted_by": raw.get("actor_id") or raw.get("created_by") or "governance-service",
                    "decision_state": state or "pending",
                    "allowedActions": {
                        "canApprove": can_decide,
                        "canReject": can_decide,
                        "canRequestRevision": state in self.PENDING_DECISION_STATES,
                    },
                    "decision_context": {
                        "risk_summary": raw.get("rationale") or "Approval decision awaiting governance action.",
                        "evidence_refs": list(raw.get("evidence_refs") or []),
                        "governance_chain": {
                            "target_type": target_type,
                            "target_id": raw.get("target_id"),
                            "target_version": raw.get("target_version"),
                        },
                        "required_approvals": 1,
                    },
                }
            )

        if decision_types:
            requested_types = {value for value in decision_types if value}
            items = [item for item in items if str(item.get("decision_type") or "") in requested_types]
        if risk_levels:
            requested_risk_levels = {value for value in risk_levels if value}
            items = [item for item in items if str(item.get("risk_level") or "") in requested_risk_levels]
        if decision_states:
            requested_states = {value for value in decision_states if value}
            items = [item for item in items if str(item.get("decision_state") or "") in requested_states]

        return [json.loads(json.dumps(item)) for item in items]

    def get_deployment_diff(self, plan_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not plan_id:
            return None
        if self._deployment_diffs_reader:
            return self._deployment_diffs_reader(plan_id)
        return None


# ---------------------------------------------------------------------------
# Combined OODA and Management Domain Port
# ---------------------------------------------------------------------------

class OodaManagementDomainPort:
    """Consolidated domain port for OODA loop packets, interventions, and review queues."""

    def __init__(
        self,
        *,
        ooda_port: Optional[OodaPacketsPort] = None,
        interventions_port: Optional[InterventionsPort] = None,
        synthesis_conflict_logs_port: Optional[SynthesisConflictLogsPort] = None,
        review_queue_port: Optional[ManagementReviewQueuePort] = None,
    ) -> None:
        self.ooda = ooda_port or OodaPacketsPort()
        self.interventions = interventions_port or InterventionsPort()
        self.conflict_logs = synthesis_conflict_logs_port or SynthesisConflictLogsPort()
        self.review_queue = review_queue_port or ManagementReviewQueuePort()

    # OODA delegates
    def list_ooda_packets(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.ooda.list_ooda_packets(**kwargs)

    def get_ooda_packet(self, packet_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return self.ooda.get_ooda_packet(packet_id)

    def list_ooda_packets_for_strategy(self, strategy_id: str) -> List[Dict[str, Any]]:
        return self.ooda.list_ooda_packets_for_strategy(strategy_id)

    def list_ooda_packets_for_runtime(self, runtime_id: str) -> List[Dict[str, Any]]:
        return self.ooda.list_ooda_packets_for_runtime(runtime_id)

    def list_ooda_packets_for_evolution_program(self, program_id: str) -> List[Dict[str, Any]]:
        return self.ooda.list_ooda_packets_for_evolution_program(program_id)

    # Intervention delegates
    def list_interventions(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.interventions.list_interventions(**kwargs)

    def get_intervention(self, intervention_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return self.interventions.get_intervention(intervention_id)

    # Synthesis conflict delegates
    def list_synthesis_conflict_logs(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.conflict_logs.list_synthesis_conflict_logs(**kwargs)

    def get_synthesis_conflict_log(self, log_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return self.conflict_logs.get_synthesis_conflict_log(log_id)

    # Review queue delegates
    def list_governance_review_queue_items(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.review_queue.list_governance_review_queue_items(**kwargs)

    def list_approval_queue_items(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.review_queue.list_approval_queue_items(**kwargs)

    def get_deployment_diff(self, plan_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return self.review_queue.get_deployment_diff(plan_id)
