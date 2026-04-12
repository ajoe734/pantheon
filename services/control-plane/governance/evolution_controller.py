"""
EvolutionController — normal-path operational evolution boundary router.

This module turns approved EvolutionDecision records into typed dispatch
commands without granting the evolution plane direct write access to runtime
or deployment objects.

Public API
----------
ActionBoundary
DispatchCommand
RollbackCommand
DispatchOutcome
ThresholdEvaluation
ThresholdEvaluator
EvolutionController
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional

from deployment_plan import DeploymentStage, RollbackActionType, RuntimeAction
from evolution_decision import (
    APPROVAL_OWNER_MATRIX,
    REVIEW_OWNER_MATRIX,
    ExecutionPlane,
    ExecutionResult,
    ExecutionStatus,
    EvolutionActionType,
    EvolutionActorRole,
    EvolutionDecision,
    EvolutionDecisionState,
    RiskLevel,
    ThresholdSignalType,
    ThresholdSnapshot,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _add_days(timestamp: str, days: int) -> str:
    moment = _parse_timestamp(timestamp)
    if moment is None:
        raise EvolutionControllerError(f"Invalid timestamp: {timestamp!r}")
    return (moment + timedelta(days=days)).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class EvolutionControllerError(ValueError):
    """Raised when controller routing or dispatch invariants are violated."""


class FreezeFollowthroughMode(str, Enum):
    GOVERNANCE_ONLY = "governance_only"
    FREEZE_STAGE = "freeze_stage"
    ROLLBACK = "rollback"


@dataclass(frozen=True)
class ActionBoundary:
    boundary_key: str
    execution_plane: ExecutionPlane | str
    threshold_policy_source: str
    reviewed_owner_roles: tuple[str, ...]
    approved_owner_roles: tuple[str, ...]
    default_cooldown_days: int
    default_observation_days: int
    followthrough: tuple[str, ...] = ()
    notes: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        payload = {
            "boundary_key": self.boundary_key,
            "execution_plane": (
                self.execution_plane.value
                if isinstance(self.execution_plane, ExecutionPlane)
                else self.execution_plane
            ),
            "threshold_policy_source": self.threshold_policy_source,
            "reviewed_owner_roles": list(self.reviewed_owner_roles),
            "approved_owner_roles": list(self.approved_owner_roles),
            "default_cooldown_days": self.default_cooldown_days,
            "default_observation_days": self.default_observation_days,
            "followthrough": list(self.followthrough),
        }
        if self.notes:
            payload["notes"] = self.notes
        return payload


@dataclass(frozen=True)
class DispatchCommand:
    command_id: str
    decision_id: str
    execution_plane: ExecutionPlane | str
    action_type: str
    target_type: str
    target_id: str
    target_version: str
    cooldown_ends_at: str
    observation_window_ends_at: str
    target_stage: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "command_id": self.command_id,
            "decision_id": self.decision_id,
            "execution_plane": (
                self.execution_plane.value
                if isinstance(self.execution_plane, ExecutionPlane)
                else self.execution_plane
            ),
            "action_type": self.action_type,
            "target_type": self.target_type,
            "target_id": self.target_id,
            "target_version": self.target_version,
            "target_stage": self.target_stage,
            "cooldown_ends_at": self.cooldown_ends_at,
            "observation_window_ends_at": self.observation_window_ends_at,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class RollbackCommand:
    command_id: str
    decision_id: str
    rollback_action_type: RollbackActionType | str
    target_binding_id: Optional[str]
    capital_pool_id: Optional[str]
    persona_id: Optional[str]
    fallback_artifact_id: Optional[str] = None
    fallback_artifact_version: Optional[str] = None
    reason: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "command_id": self.command_id,
            "decision_id": self.decision_id,
            "rollback_action_type": (
                self.rollback_action_type.value
                if isinstance(self.rollback_action_type, RollbackActionType)
                else self.rollback_action_type
            ),
            "target_binding_id": self.target_binding_id,
            "capital_pool_id": self.capital_pool_id,
            "persona_id": self.persona_id,
            "fallback_artifact_id": self.fallback_artifact_id,
            "fallback_artifact_version": self.fallback_artifact_version,
            "reason": self.reason,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class DispatchOutcome:
    boundary: ActionBoundary
    primary_command: DispatchCommand
    execution_result: ExecutionResult
    followthrough_commands: List[DispatchCommand] = field(default_factory=list)
    rollback_command: Optional[RollbackCommand] = None

    def to_dict(self) -> Dict[str, Any]:
        payload = {
            "boundary": self.boundary.to_dict(),
            "primary_command": self.primary_command.to_dict(),
            "execution_result": self.execution_result.to_dict(),
            "followthrough_commands": [command.to_dict() for command in self.followthrough_commands],
        }
        if self.rollback_command is not None:
            payload["rollback_command"] = self.rollback_command.to_dict()
        return payload


@dataclass(frozen=True)
class ThresholdEvaluation:
    proposed_action: EvolutionActionType | str
    rationale: str
    requires_runtime_followthrough: bool = False
    committee_review_required: bool = False
    notes: tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "proposed_action": (
                self.proposed_action.value
                if isinstance(self.proposed_action, EvolutionActionType)
                else self.proposed_action
            ),
            "rationale": self.rationale,
            "requires_runtime_followthrough": self.requires_runtime_followthrough,
            "committee_review_required": self.committee_review_required,
            "notes": list(self.notes),
        }


class ThresholdEvaluator:
    """Maps threshold snapshots to the normal-path proposed action family."""

    def classify(
        self,
        snapshot: ThresholdSnapshot,
        *,
        context: Optional[Mapping[str, Any]] = None,
    ) -> ThresholdEvaluation:
        ctx = dict(context or {})
        signal = ThresholdSignalType(snapshot.signal_type)
        metric = snapshot.metric_name
        observed = snapshot.observed_value

        if signal == ThresholdSignalType.PERFORMANCE_DEGRADATION:
            if metric in {"sharpe_pct_of_baseline", "sharpe_ratio_pct_of_baseline"} and float(observed) < 0.5:
                return ThresholdEvaluation(
                    proposed_action=EvolutionActionType.RETRAIN,
                    rationale="Sharpe fell below 50% of baseline over the policy window.",
                )
            if metric in {"rolling_drawdown_multiple", "drawdown_multiple"} and float(observed) > 1.25:
                return ThresholdEvaluation(
                    proposed_action=EvolutionActionType.RETRAIN,
                    rationale="Rolling drawdown exceeded 1.25x expected tolerance.",
                )
            if metric in {"evaluation_windows_below_baseline", "consecutive_underperforming_windows"} and int(observed) >= 3:
                return ThresholdEvaluation(
                    proposed_action=EvolutionActionType.FLAG_FOR_REVIEW,
                    rationale="Three consecutive evaluation windows underperformed baseline.",
                )

        if signal == ThresholdSignalType.EXECUTION_DRIFT:
            if metric == "slippage_drift_pct" and float(observed) > 25.0:
                return ThresholdEvaluation(
                    proposed_action=EvolutionActionType.REVALIDATE,
                    rationale="Slippage drift exceeded the 25% revalidation threshold.",
                )
            if metric == "order_reject_rate_pct" and float(observed) > 1.0:
                if ctx.get("incident_open"):
                    return ThresholdEvaluation(
                        proposed_action=EvolutionActionType.FREEZE,
                        rationale="Reject-rate drift escalated into an active incident.",
                        requires_runtime_followthrough=bool(ctx.get("has_active_runtime")),
                    )
                return ThresholdEvaluation(
                    proposed_action=EvolutionActionType.TIGHTEN_RISK_POLICY,
                    rationale="Reject rate exceeded 1.0% without opening an incident yet.",
                )
            if metric in {"partial_fill_anomaly_days", "partial_fill_timeout_anomaly_days"} and int(observed) >= 3:
                return ThresholdEvaluation(
                    proposed_action=EvolutionActionType.FLAG_FOR_REVIEW,
                    rationale="Partial-fill anomaly persisted for three consecutive days.",
                )

        if signal == ThresholdSignalType.FEATURE_DRIFT:
            if metric in {"population_stability_index", "psi"}:
                psi = float(observed)
                if psi > 0.30:
                    return ThresholdEvaluation(
                        proposed_action=EvolutionActionType.REVALIDATE,
                        rationale="PSI exceeded the hard revalidation threshold of 0.30.",
                    )
                if psi > 0.20:
                    return ThresholdEvaluation(
                        proposed_action=EvolutionActionType.OBSERVE,
                        rationale="PSI exceeded the observation threshold of 0.20.",
                    )
            if metric == "label_mismatch_rate_pct" and float(observed) > 0.5:
                return ThresholdEvaluation(
                    proposed_action=EvolutionActionType.RETRAIN,
                    rationale="Label mismatch exceeded the retrain threshold of 0.5%.",
                )

        if signal == ThresholdSignalType.HUMAN_CORRECTION:
            if metric == "major_corrections_in_5_sessions" and int(observed) > 3:
                return ThresholdEvaluation(
                    proposed_action=EvolutionActionType.RETRAIN,
                    rationale="Human correction exceeded 3 major fixes in 5 sessions.",
                )
            if metric == "same_strategy_rejects_14d" and int(observed) >= 2:
                return ThresholdEvaluation(
                    proposed_action=EvolutionActionType.FLAG_FOR_REVIEW,
                    rationale="The same strategy was rejected at least twice in 14 days.",
                )

        if signal == ThresholdSignalType.GOVERNANCE_INCIDENT:
            severity = str(ctx.get("incident_severity", "")).lower()
            if metric == "severity1_incident_count" and float(observed) >= 1:
                return ThresholdEvaluation(
                    proposed_action=EvolutionActionType.FREEZE,
                    rationale="Severity-1 incident opened the high-risk freeze path.",
                    requires_runtime_followthrough=bool(ctx.get("has_active_runtime")),
                )
            if metric == "severity2_incident_count_30d" and int(observed) >= 2:
                return ThresholdEvaluation(
                    proposed_action=EvolutionActionType.FREEZE,
                    rationale="Repeated Severity-2 incidents escalated to a freeze proposal.",
                    requires_runtime_followthrough=bool(ctx.get("has_active_runtime")),
                )
            if metric == "unresolved_loader_binding_approval_mismatch_count" and int(observed) >= 1:
                return ThresholdEvaluation(
                    proposed_action=EvolutionActionType.FREEZE,
                    rationale="An unresolved loader/binding/approval mismatch requires freeze review.",
                    requires_runtime_followthrough=bool(ctx.get("has_active_runtime")),
                )
            if metric == "rollback_problem_persists" and bool(observed):
                return ThresholdEvaluation(
                    proposed_action=EvolutionActionType.FREEZE,
                    rationale="Rollback executed but the problem persists, requiring committee review.",
                    requires_runtime_followthrough=bool(ctx.get("has_active_runtime")),
                    committee_review_required=True,
                    notes=("rollback_escalation",),
                )
            if severity in {"critical", "severity1"}:
                return ThresholdEvaluation(
                    proposed_action=EvolutionActionType.FREEZE,
                    rationale="Critical incident severity triggered an immediate freeze proposal.",
                    requires_runtime_followthrough=bool(ctx.get("has_active_runtime")),
                )

        if signal == ThresholdSignalType.MANUAL_REVIEW:
            return ThresholdEvaluation(
                proposed_action=EvolutionActionType.FLAG_FOR_REVIEW,
                rationale="Manual review trigger requests an explicit reviewer decision.",
            )

        raise EvolutionControllerError(
            f"No threshold mapping is defined for signal={signal.value!r}, metric={metric!r}, observed={observed!r}"
        )


class EvolutionController:
    """Routes approved EvolutionDecision records to the correct downstream plane."""

    _RESEARCH_ACTIONS = {
        EvolutionActionType.OBSERVE,
        EvolutionActionType.REVALIDATE,
        EvolutionActionType.RETRAIN,
        EvolutionActionType.REQUIRE_MORE_DATA,
        EvolutionActionType.FLAG_FOR_REVIEW,
    }

    _GOVERNANCE_ACTIONS = {
        EvolutionActionType.REDUCE_BUDGET,
        EvolutionActionType.TIGHTEN_RISK_POLICY,
        EvolutionActionType.MUTATE_PERSONA_ROUTE_POLICY,
        EvolutionActionType.MUTATE_CONSULT_POLICY,
        EvolutionActionType.FREEZE,
        EvolutionActionType.RETIRE,
        EvolutionActionType.SPLIT_PERSONA,
        EvolutionActionType.MERGE_PERSONA,
        EvolutionActionType.REMOVE_LIVE_OWNER_ROLE,
        EvolutionActionType.RESTRICT_POOL_ELIGIBILITY,
        EvolutionActionType.REVIVE,
    }

    def boundary_for(
        self,
        decision: EvolutionDecision,
        *,
        has_active_runtime: bool = False,
    ) -> ActionBoundary:
        action = EvolutionActionType(decision.action_type)
        risk = RiskLevel(decision.risk_level)
        reviewed_roles = tuple(
            sorted(role.value for role in REVIEW_OWNER_MATRIX[risk])
        )
        approved_roles = tuple(
            sorted(role.value for role in APPROVAL_OWNER_MATRIX[risk])
        )

        if action == EvolutionActionType.FREEZE:
            target_stage = decision.target_stage
            if target_stage == DeploymentStage.LIVE.value and has_active_runtime:
                return ActionBoundary(
                    boundary_key="freeze_live_active_runtime",
                    execution_plane=ExecutionPlane.GOVERNANCE,
                    threshold_policy_source="EVOLUTION_REVIEW_AND_THRESHOLDS.md §7.5-§7.6",
                    reviewed_owner_roles=reviewed_roles,
                    approved_owner_roles=approved_roles,
                    default_cooldown_days=14,
                    default_observation_days=14,
                    followthrough=("deployment.freeze_stage", "runtime.rollback"),
                    notes="Freeze is the governance decision. DeploymentPlan or RollbackCommand carries operational follow-through.",
                )
            if target_stage == DeploymentStage.LIVE.value:
                return ActionBoundary(
                    boundary_key="freeze_live_no_active_runtime",
                    execution_plane=ExecutionPlane.GOVERNANCE,
                    threshold_policy_source="EVOLUTION_REVIEW_AND_THRESHOLDS.md §7.5-§7.6",
                    reviewed_owner_roles=reviewed_roles,
                    approved_owner_roles=approved_roles,
                    default_cooldown_days=14,
                    default_observation_days=14,
                    notes="No active runtime exists, so the path is governance-only despite high-risk ownership.",
                )
            return ActionBoundary(
                boundary_key="freeze_non_live",
                execution_plane=ExecutionPlane.GOVERNANCE,
                threshold_policy_source="EVOLUTION_REVIEW_AND_THRESHOLDS.md §7.3-§7.6",
                reviewed_owner_roles=reviewed_roles,
                approved_owner_roles=approved_roles,
                default_cooldown_days=7,
                default_observation_days=7,
                followthrough=("deployment.freeze_stage",) if has_active_runtime else (),
                notes="Freeze defaults to governance quarantine. Stage-freeze follow-through is optional on active paper/canary runtimes.",
            )

        if action in self._RESEARCH_ACTIONS:
            return ActionBoundary(
                boundary_key=f"research_{action.value}",
                execution_plane=ExecutionPlane.RESEARCH,
                threshold_policy_source="EVOLUTION_REVIEW_AND_THRESHOLDS.md §7.1-§7.4",
                reviewed_owner_roles=reviewed_roles,
                approved_owner_roles=approved_roles,
                default_cooldown_days=3,
                default_observation_days=7,
                notes="Execution means a governed research work item or job was accepted; no deploy/runtime mutation occurs here.",
            )

        if action == EvolutionActionType.FORCE_RISK_OFF:
            return ActionBoundary(
                boundary_key="force_risk_off_runtime",
                execution_plane=ExecutionPlane.RUNTIME,
                threshold_policy_source="EVOLUTION_REVIEW_AND_THRESHOLDS.md §7.5-§7.6",
                reviewed_owner_roles=reviewed_roles,
                approved_owner_roles=approved_roles,
                default_cooldown_days=14,
                default_observation_days=14,
                followthrough=("runtime.rollback",),
                notes="Force-risk-off does not create a parallel evolution command surface; it emits a runtime mitigation request.",
            )

        if action in self._GOVERNANCE_ACTIONS:
            default_days = 7 if risk == RiskLevel.MEDIUM else 14
            return ActionBoundary(
                boundary_key=f"governance_{action.value}",
                execution_plane=ExecutionPlane.GOVERNANCE,
                threshold_policy_source="EVOLUTION_REVIEW_AND_THRESHOLDS.md §7.x",
                reviewed_owner_roles=reviewed_roles,
                approved_owner_roles=approved_roles,
                default_cooldown_days=default_days,
                default_observation_days=default_days,
            )

        raise EvolutionControllerError(f"Unsupported action_type for dispatch: {decision.action_type!r}")

    def dispatch_approved(
        self,
        decision: EvolutionDecision,
        *,
        executed_at: Optional[str] = None,
        has_active_runtime: bool = False,
        active_binding_id: Optional[str] = None,
        freeze_mode: FreezeFollowthroughMode | str = FreezeFollowthroughMode.GOVERNANCE_ONLY,
        rollback_action_type: RollbackActionType | str | None = None,
        fallback_artifact_id: Optional[str] = None,
        fallback_artifact_version: Optional[str] = None,
        force_stage_freeze: bool = False,
    ) -> DispatchOutcome:
        if EvolutionDecisionState(decision.decision_state) != EvolutionDecisionState.APPROVED:
            raise EvolutionControllerError(
                f"dispatch_approved requires an approved EvolutionDecision, got {decision.decision_state!r}"
            )

        boundary = self.boundary_for(decision, has_active_runtime=has_active_runtime)
        accepted_at = executed_at or utc_now()
        cooldown_ends_at = _add_days(accepted_at, boundary.default_cooldown_days)
        observation_window_ends_at = _add_days(accepted_at, boundary.default_observation_days)
        primary_command = DispatchCommand(
            command_id=f"dispatch-{decision.decision_id}",
            decision_id=decision.decision_id,
            execution_plane=boundary.execution_plane,
            action_type=str(decision.action_type),
            target_type=str(decision.target_type),
            target_id=decision.target_id,
            target_version=decision.target_version,
            target_stage=decision.target_stage,
            cooldown_ends_at=cooldown_ends_at,
            observation_window_ends_at=observation_window_ends_at,
            metadata={
                "approval_decision_id": decision.approval_decision_id,
                "boundary_key": boundary.boundary_key,
                "linked_incident_id": decision.linked_incident_id,
                "linked_postmortem_id": decision.linked_postmortem_id,
                "followthrough": list(boundary.followthrough),
            },
        )

        followthrough_commands: List[DispatchCommand] = []
        rollback_command: RollbackCommand | None = None

        action = EvolutionActionType(decision.action_type)
        requested_freeze_mode = FreezeFollowthroughMode(freeze_mode)
        if force_stage_freeze:
            requested_freeze_mode = FreezeFollowthroughMode.FREEZE_STAGE

        if action == EvolutionActionType.FREEZE and has_active_runtime:
            if requested_freeze_mode == FreezeFollowthroughMode.FREEZE_STAGE:
                followthrough_commands.append(
                    DispatchCommand(
                        command_id=f"dispatch-{decision.decision_id}-freeze-stage",
                        decision_id=decision.decision_id,
                        execution_plane=ExecutionPlane.DEPLOYMENT,
                        action_type="freeze_stage",
                        target_type=str(decision.target_type),
                        target_id=decision.target_id,
                        target_version=decision.target_version,
                        target_stage=DeploymentStage.FROZEN.value,
                        cooldown_ends_at=cooldown_ends_at,
                        observation_window_ends_at=observation_window_ends_at,
                        metadata={
                            "current_stage": decision.target_stage,
                            "target_stage": DeploymentStage.FROZEN.value,
                            "runtime_action": RuntimeAction.FREEZE_BINDING.value,
                            "parent_decision_id": decision.decision_id,
                        },
                    )
                )
            elif requested_freeze_mode == FreezeFollowthroughMode.ROLLBACK:
                selected_action = RollbackActionType(
                    rollback_action_type or RollbackActionType.PAUSE_THEN_REPLACE.value
                )
                rollback_command = RollbackCommand(
                    command_id=f"rollback-{decision.decision_id}",
                    decision_id=decision.decision_id,
                    rollback_action_type=selected_action,
                    target_binding_id=active_binding_id,
                    capital_pool_id=decision.capital_pool_id,
                    persona_id=decision.persona_id,
                    fallback_artifact_id=fallback_artifact_id,
                    fallback_artifact_version=fallback_artifact_version,
                    reason="Approved freeze requires runtime mitigation on an active deployment.",
                    metadata={
                        "parent_decision_id": decision.decision_id,
                        "target_stage": decision.target_stage,
                    },
                )

        if action == EvolutionActionType.FORCE_RISK_OFF:
            if not has_active_runtime:
                raise EvolutionControllerError("force_risk_off requires an active runtime to mitigate")
            selected_action = RollbackActionType(
                rollback_action_type or RollbackActionType.LIQUIDATE_THEN_REPLACE.value
            )
            rollback_command = RollbackCommand(
                command_id=f"rollback-{decision.decision_id}",
                decision_id=decision.decision_id,
                rollback_action_type=selected_action,
                target_binding_id=active_binding_id,
                capital_pool_id=decision.capital_pool_id,
                persona_id=decision.persona_id,
                fallback_artifact_id=fallback_artifact_id,
                fallback_artifact_version=fallback_artifact_version,
                reason="Force-risk-off requires immediate runtime mitigation.",
                metadata={"parent_decision_id": decision.decision_id},
            )

        execution_result = ExecutionResult(
            status=ExecutionStatus.SUBMITTED,
            plane=boundary.execution_plane,
            executed_at=accepted_at,
            execution_ref_id=primary_command.command_id,
            outcome_summary=self._summarize_outcome(
                boundary,
                followthrough_commands=followthrough_commands,
                rollback_command=rollback_command,
            ),
        )
        return DispatchOutcome(
            boundary=boundary,
            primary_command=primary_command,
            execution_result=execution_result,
            followthrough_commands=followthrough_commands,
            rollback_command=rollback_command,
        )

    def execute_approved(
        self,
        decision: EvolutionDecision,
        *,
        actor_role: EvolutionActorRole | str = EvolutionActorRole.EVOLUTION_CONTROLLER,
        actor_id: str = "evolution-controller",
        executed_at: Optional[str] = None,
        has_active_runtime: bool = False,
        active_binding_id: Optional[str] = None,
        freeze_mode: FreezeFollowthroughMode | str = FreezeFollowthroughMode.GOVERNANCE_ONLY,
        rollback_action_type: RollbackActionType | str | None = None,
        fallback_artifact_id: Optional[str] = None,
        fallback_artifact_version: Optional[str] = None,
        force_stage_freeze: bool = False,
    ) -> DispatchOutcome:
        outcome = self.dispatch_approved(
            decision,
            executed_at=executed_at,
            has_active_runtime=has_active_runtime,
            active_binding_id=active_binding_id,
            freeze_mode=freeze_mode,
            rollback_action_type=rollback_action_type,
            fallback_artifact_id=fallback_artifact_id,
            fallback_artifact_version=fallback_artifact_version,
            force_stage_freeze=force_stage_freeze,
        )
        decision.execute(
            actor_role,
            actor_id,
            outcome.execution_result,
            cooldown_ends_at=outcome.primary_command.cooldown_ends_at,
            observation_window_ends_at=outcome.primary_command.observation_window_ends_at,
            note=outcome.execution_result.outcome_summary,
        )
        return outcome

    def create_redeploy_followthrough(
        self,
        parent_decision: EvolutionDecision,
        *,
        artifact_id: str,
        artifact_version: str,
        approval_decision_id: str,
        target_stage: DeploymentStage | str,
        requested_at: Optional[str] = None,
        sponsor_persona_id: Optional[str] = None,
    ) -> DispatchCommand:
        if EvolutionDecisionState(parent_decision.decision_state) != EvolutionDecisionState.EXECUTED:
            raise EvolutionControllerError("Redeploy follow-through requires an executed parent EvolutionDecision")
        normalized_stage = DeploymentStage(target_stage)
        if normalized_stage not in {DeploymentStage.PAPER, DeploymentStage.CANARY, DeploymentStage.LIVE}:
            raise EvolutionControllerError("Redeploy follow-through target_stage must be paper, canary, or live")
        if not approval_decision_id:
            raise EvolutionControllerError("Redeploy follow-through requires a fresh approval_decision_id")

        start = _parse_timestamp(parent_decision.observation_window_started_at)
        end = _parse_timestamp(parent_decision.observation_window_ends_at)
        moment = _parse_timestamp(requested_at) or start
        if start is None or end is None or moment is None:
            raise EvolutionControllerError("Parent EvolutionDecision must already be in observation")
        if moment < start or moment > end:
            raise EvolutionControllerError("Redeploy follow-through must occur during the parent observation window")

        if parent_decision.cooldown_ends_at is None or parent_decision.observation_window_ends_at is None:
            raise EvolutionControllerError("Executed parent decision must carry inherited cooldown / observation windows")

        if normalized_stage == DeploymentStage.PAPER:
            reviewed_owner_roles = ("reviewer_on_duty", "automated_gate")
            approved_owner_roles = ("reviewer_on_duty", "automated_gate")
        else:
            reviewed_owner_roles = ("reviewer", "risk_owner", "operator")
            approved_owner_roles = ("reviewer", "risk_owner", "operator")

        metadata = {
            "parent_decision_id": parent_decision.decision_id,
            "parent_action_type": str(parent_decision.action_type),
            "approval_decision_id": approval_decision_id,
            "sponsor_persona_id": sponsor_persona_id or parent_decision.persona_id,
            "reviewed_owner_roles": list(reviewed_owner_roles),
            "approved_owner_roles": list(approved_owner_roles),
            "policy_source": "PAPER_CANARY_LIVE_POLICY.md §5-§8",
            "requires_new_deployment_plan": True,
        }
        return DispatchCommand(
            command_id=f"dispatch-{parent_decision.decision_id}-redeploy",
            decision_id=parent_decision.decision_id,
            execution_plane=ExecutionPlane.DEPLOYMENT,
            action_type="redeploy_followthrough",
            target_type=str(parent_decision.target_type),
            target_id=artifact_id,
            target_version=artifact_version,
            target_stage=normalized_stage.value,
            cooldown_ends_at=parent_decision.cooldown_ends_at,
            observation_window_ends_at=parent_decision.observation_window_ends_at,
            metadata=metadata,
        )

    def _summarize_outcome(
        self,
        boundary: ActionBoundary,
        *,
        followthrough_commands: List[DispatchCommand],
        rollback_command: RollbackCommand | None,
    ) -> str:
        parts = [f"{boundary.boundary_key} submitted to {boundary.execution_plane.value if isinstance(boundary.execution_plane, ExecutionPlane) else boundary.execution_plane}"]
        if followthrough_commands:
            parts.append(f"{len(followthrough_commands)} follow-through command(s) emitted")
        if rollback_command is not None:
            action = (
                rollback_command.rollback_action_type.value
                if isinstance(rollback_command.rollback_action_type, RollbackActionType)
                else rollback_command.rollback_action_type
            )
            parts.append(f"rollback companion emitted ({action})")
        return "; ".join(parts) + "."
