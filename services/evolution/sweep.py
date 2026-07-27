"""Daily sweep and threshold-trigger proposal builder for EvolutionDecision.

The sweep is intentionally proposal-only. It reads IncidentCase records that
were already written by the incident domain, derives one governed
EvolutionDecision proposal per incident target, and lets the existing
EvolutionDecisionStore single-active rule enforce cooldown/convergence.
"""
from __future__ import annotations

import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from services.incident.incident import IncidentCase, IncidentStore

_CP_GOV = Path(__file__).resolve().parent.parent / "control-plane" / "governance"
if str(_CP_GOV) not in sys.path:
    sys.path.insert(0, str(_CP_GOV))

from approval_decision import EvidenceRef, EvidenceRefType  # type: ignore
from evolution_controller import EvolutionControllerError, ThresholdEvaluator  # type: ignore
from evolution_decision import (  # type: ignore
    ComparisonOperator,
    DEFAULT_TENANT_ID,
    EvolutionActionType,
    EvolutionDecision,
    EvolutionDecisionError,
    EvolutionDecisionStore,
    EvolutionTargetType,
    ThresholdSignalType,
    ThresholdSnapshot,
)


@dataclass(frozen=True)
class SweepItem:
    incident_id: str
    status: str
    target_type: str | None = None
    target_id: str | None = None
    decision_id: str | None = None
    action_type: str | None = None
    active_decision_id: str | None = None
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value is not None}


@dataclass(frozen=True)
class DailySweepResult:
    sweep_id: str
    scanned_incidents: int
    created_decisions: int
    existing_decisions: int
    cooldown_blocked: int
    skipped_incidents: int
    items: list[SweepItem] = field(default_factory=list)
    scheduler_attach: dict[str, str] = field(
        default_factory=lambda: {
            "route": "POST /api/evolution/daily-sweep",
            "worker_module": "services.evolution.scheduler_worker",
            "compose_profile": "evolution-daily-sweep-scheduler",
        }
    )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["items"] = [item.to_dict() for item in self.items]
        return payload


def run_daily_sweep(
    *,
    incident_store: IncidentStore,
    decision_store: EvolutionDecisionStore,
    incident_ids: Sequence[str] | None = None,
    include_closed: bool = False,
    max_incidents: int | None = None,
    sweep_id: str = "daily",
    evaluator: ThresholdEvaluator | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
) -> DailySweepResult:
    """Create EvolutionDecision proposals for eligible incident triggers."""
    incidents = _select_incidents(
        incident_store=incident_store,
        incident_ids=incident_ids,
        include_closed=include_closed,
    )
    if max_incidents is not None:
        incidents = incidents[:max_incidents]

    classifier = evaluator or ThresholdEvaluator()
    items: list[SweepItem] = []

    for incident in incidents:
        target_type = EvolutionTargetType.CANDIDATE_ARTIFACT.value
        target_id = incident.artifact_id
        existing = _decision_for_incident(
            decision_store,
            incident.incident_id,
            tenant_id=tenant_id,
        )
        if existing is not None:
            items.append(
                SweepItem(
                    incident_id=incident.incident_id,
                    status="existing",
                    target_type=target_type,
                    target_id=target_id,
                    decision_id=existing.decision_id,
                    action_type=str(_enum_value(existing.action_type)),
                    reason="incident already has a linked EvolutionDecision",
                )
            )
            continue

        active = decision_store.find_active_by_target(
            target_type,
            target_id,
            tenant_id=tenant_id,
        )
        if active:
            items.append(
                SweepItem(
                    incident_id=incident.incident_id,
                    status="cooldown_blocked",
                    target_type=target_type,
                    target_id=target_id,
                    active_decision_id=active[0].decision_id,
                    reason=(
                        "target already has an active EvolutionDecision; "
                        "cooldown/observation single-active-rule blocks a new proposal"
                    ),
                )
            )
            continue

        try:
            decision = build_decision_from_incident(
                incident,
                evaluator=classifier,
                sweep_id=sweep_id,
                tenant_id=tenant_id,
            )
            decision_store.put(decision)
        except (EvolutionControllerError, EvolutionDecisionError, ValueError, TypeError) as exc:
            items.append(
                SweepItem(
                    incident_id=incident.incident_id,
                    status="skipped",
                    target_type=target_type,
                    target_id=target_id,
                    reason=str(exc),
                )
            )
            continue

        items.append(
            SweepItem(
                incident_id=incident.incident_id,
                status="created",
                target_type=target_type,
                target_id=target_id,
                decision_id=decision.decision_id,
                action_type=str(_enum_value(decision.action_type)),
            )
        )

    return DailySweepResult(
        sweep_id=sweep_id,
        scanned_incidents=len(incidents),
        created_decisions=sum(1 for item in items if item.status == "created"),
        existing_decisions=sum(1 for item in items if item.status == "existing"),
        cooldown_blocked=sum(1 for item in items if item.status == "cooldown_blocked"),
        skipped_incidents=sum(1 for item in items if item.status == "skipped"),
        items=items,
    )


def build_decision_from_incident(
    incident: IncidentCase,
    *,
    evaluator: ThresholdEvaluator | None = None,
    sweep_id: str = "daily",
    tenant_id: str = DEFAULT_TENANT_ID,
) -> EvolutionDecision:
    """Derive one proposed EvolutionDecision from an incident threshold signal."""
    snapshot = threshold_snapshot_from_incident(incident)
    evaluation = (evaluator or ThresholdEvaluator()).classify(
        snapshot,
        context={
            "incident_open": incident.is_open(),
            "incident_severity": incident.severity,
            "has_active_runtime": bool(incident.binding_id),
        },
    )
    action_type = str(evaluation.proposed_action.value if hasattr(evaluation.proposed_action, "value") else evaluation.proposed_action)
    metadata = {
        "source": "evolution_daily_sweep",
        "trigger_source": "threshold-triggered + daily sweep",
        "sweep_id": sweep_id,
        "source_incident_id": incident.incident_id,
        "source_trace_id": incident.trace_id,
        "runtime_binding_id": incident.binding_id,
        "deployment_plan_id": incident.deployment_plan_id,
        "persona_capital_binding_id": incident.persona_capital_binding_id,
        "runtime_id": incident.runtime_id,
        "deployment_stage_snapshot": incident.deployment_stage,
        "threshold_evaluation": evaluation.to_dict(),
        "proposal_only": True,
        "live_mutation_allowed": False,
        "runtime_binding_mutation_allowed": False,
        "broker_order_allowed": False,
        "capital_binding_mutation_allowed": False,
    }
    return EvolutionDecision.create_proposed(
        decision_id=_decision_id_for_incident(incident.incident_id),
        target_type=EvolutionTargetType.CANDIDATE_ARTIFACT,
        target_id=incident.artifact_id,
        target_version=incident.artifact_version,
        action_type=EvolutionActionType(action_type),
        rationale=(
            f"Daily evolution sweep derived a governed {action_type} proposal "
            f"from incident {incident.incident_id}. The proposal is review-gated "
            "and has no runtime, broker, or capital-binding side effects."
        ),
        created_by_id="evolution-daily-sweep",
        threshold_snapshots=[snapshot],
        evidence_refs=_evidence_refs_for_incident(incident),
        linked_incident_id=incident.incident_id,
        capital_pool_id=incident.capital_pool_id,
        target_stage=incident.deployment_stage,
        metadata=metadata,
        tenant_id=tenant_id,
    )


def threshold_snapshot_from_incident(incident: IncidentCase) -> ThresholdSnapshot:
    fields = _parse_evidence_summary(incident.evidence_summary or "")
    metric_name = fields.get("threshold_metric")
    if not metric_name:
        if incident.severity == "critical":
            return ThresholdSnapshot(
                policy_source="EVOLUTION_REVIEW_AND_THRESHOLDS.md section 7.5",
                signal_type=ThresholdSignalType.GOVERNANCE_INCIDENT,
                metric_name="severity1_incident_count",
                comparator=ComparisonOperator.GTE,
                observed_value=1,
                threshold_value=1,
                window="active-incident",
                breached=True,
                note="Critical IncidentCase enters the high-risk freeze proposal path.",
            )
        return ThresholdSnapshot(
            policy_source="EVOLUTION_REVIEW_AND_THRESHOLDS.md section 7.5",
            signal_type=ThresholdSignalType.MANUAL_REVIEW,
            metric_name="incident_followup_required",
            comparator=ComparisonOperator.GTE,
            observed_value=1,
            threshold_value=1,
            window="active-incident",
            breached=True,
            note="IncidentCase requires explicit evolution review.",
        )

    return ThresholdSnapshot(
        policy_source=fields.get("policy_source") or "EVOLUTION_REVIEW_AND_THRESHOLDS.md",
        signal_type=_signal_for_metric(metric_name),
        metric_name=metric_name,
        comparator=fields.get("comparator") or ComparisonOperator.GT.value,
        observed_value=_typed_value(fields.get("observed")),
        threshold_value=_typed_value(fields.get("threshold_value")),
        window=fields.get("threshold_window"),
        breached=True,
        note=f"Derived from IncidentCase {incident.incident_id} evidence summary.",
    )


def _select_incidents(
    *,
    incident_store: IncidentStore,
    incident_ids: Sequence[str] | None,
    include_closed: bool,
) -> list[IncidentCase]:
    if incident_ids:
        incidents = []
        for incident_id in incident_ids:
            incident = incident_store.get_incident(str(incident_id))
            if incident is not None:
                incidents.append(incident)
        return sorted(incidents, key=lambda item: (item.created_at, item.incident_id))
    if include_closed:
        incidents = incident_store.list_incidents()
    else:
        incidents = incident_store.find_open_incidents()
    return sorted(incidents, key=lambda item: (item.created_at, item.incident_id))


def _decision_for_incident(
    decision_store: EvolutionDecisionStore,
    incident_id: str,
    *,
    tenant_id: str = DEFAULT_TENANT_ID,
) -> EvolutionDecision | None:
    for decision in decision_store.list_all():
        if (
            decision.tenant_id == tenant_id
            and decision.linked_incident_id == incident_id
        ):
            return decision
    return None


def _evidence_refs_for_incident(incident: IncidentCase) -> list[EvidenceRef]:
    refs = [
        EvidenceRef(
            ref_type=EvidenceRefType.MANUAL_REVIEW_TICKET,
            ref_id=incident.incident_id,
            storage_ref={"backend": "incident_store", "object_type": "IncidentCase"},
            note="IncidentCase evidence snapshot for daily evolution sweep proposal.",
        )
    ]
    for event_id in incident.telemetry_event_ids:
        refs.append(
            EvidenceRef(
                ref_type=EvidenceRefType.TELEMETRY_SUMMARY,
                ref_id=event_id,
                storage_ref={"backend": "telemetry", "object_type": "TelemetryEvent"},
                note="Telemetry event linked to the threshold-triggered incident.",
            )
        )
    return refs


def _parse_evidence_summary(summary: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for part in summary.split(";"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        normalized_key = key.strip().lower().replace(" ", "_")
        normalized_value = value.strip()
        if normalized_key and normalized_value:
            fields[normalized_key] = normalized_value
    return fields


def _signal_for_metric(metric_name: str) -> str:
    metric = metric_name.lower()
    if any(token in metric for token in ("sharpe", "drawdown", "underperform", "baseline")):
        return ThresholdSignalType.PERFORMANCE_DEGRADATION.value
    if any(token in metric for token in ("slippage", "reject", "partial_fill", "timeout")):
        return ThresholdSignalType.EXECUTION_DRIFT.value
    if any(token in metric for token in ("psi", "population_stability", "label", "feature")):
        return ThresholdSignalType.FEATURE_DRIFT.value
    if "severity" in metric or "incident" in metric:
        return ThresholdSignalType.GOVERNANCE_INCIDENT.value
    return ThresholdSignalType.MANUAL_REVIEW.value


def _typed_value(value: str | None) -> Any:
    if value is None:
        return 1
    try:
        number = float(value)
    except ValueError:
        return value
    return int(number) if number.is_integer() else number


def _enum_value(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value


def _decision_id_for_incident(incident_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", incident_id.strip()).strip("-._")
    return f"evo-sweep-{safe or 'incident'}"
