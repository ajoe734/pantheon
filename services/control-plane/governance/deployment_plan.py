"""
DeploymentPlan — Governance Deployment Contract

First-class deployment-planning object that turns an approved artifact into an
explicit paper / canary / live / frozen stage transition plan. Shared by the
governance plane and consumed by runtime orchestration.

Public API
----------
DeploymentPlan            — immutable deployment-intent dataclass
StagePlanner              — transition planner and policy validator
DeploymentPlanStore       — in-memory store with optional JSON persistence
validate_plan()           — semantic validation helper
validate_plan_json()      — structural validation helper

Schema
------
The canonical JSON schema lives at:
    services/control-plane/governance/deployment_plan.schema.json
"""
from __future__ import annotations

import copy
import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from services.capital.risk_policy import (
    RiskPolicy,
    RiskPolicyEvaluation,
    RiskPolicyEvaluationContext,
    RiskPolicyEvaluator,
    RiskPolicyTargetType,
    risk_policy_rejection_message,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized)


class DeploymentPlanError(ValueError):
    """Raised when deployment-plan planning or validation fails."""


class DeploymentStage(str, Enum):
    NONE = "none"
    PAPER = "paper"
    CANARY = "canary"
    LIVE = "live"
    FROZEN = "frozen"


class TransitionType(str, Enum):
    ACTIVATE = "activate"
    PROMOTE = "promote"
    ROLLBACK = "rollback"
    FREEZE = "freeze"
    RESUME = "resume"


class RuntimeAction(str, Enum):
    DEPLOY_NEW_BINDING = "deploy_new_binding"
    REPLACE_BINDING = "replace_binding"
    FREEZE_BINDING = "freeze_binding"
    RESUME_BINDING = "resume_binding"
    PAUSE_THEN_REPLACE = "pause_then_replace"
    LIQUIDATE_THEN_REPLACE = "liquidate_then_replace"


class RollbackActionType(str, Enum):
    REPLACE = "replace"
    PAUSE_THEN_REPLACE = "pause_then_replace"
    LIQUIDATE_THEN_REPLACE = "liquidate_then_replace"


class PlanStatus(str, Enum):
    DRAFT = "draft"
    APPROVED = "approved"
    EXECUTING = "executing"
    EXECUTED = "executed"
    ABORTED = "aborted"
    REJECTED = "rejected"
    FAILED = "failed"


class DecisionOutcome(str, Enum):
    APPROVED = "approved"
    APPROVED_WITH_CONDITIONS = "approved_with_conditions"


class DecisionState(str, Enum):
    DECIDED = "decided"


@dataclass
class ScheduleWindow:
    start_at: str
    end_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        result = {"start_at": self.start_at}
        if self.end_at:
            result["end_at"] = self.end_at
        return result

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ScheduleWindow":
        return cls(start_at=str(data["start_at"]), end_at=data.get("end_at"))


@dataclass
class DeploymentScale:
    capital_scale_pct: float
    gross_scale_pct: float
    ramp_schedule: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "capital_scale_pct": self.capital_scale_pct,
            "gross_scale_pct": self.gross_scale_pct,
            "ramp_schedule": list(self.ramp_schedule),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DeploymentScale":
        return cls(
            capital_scale_pct=float(data["capital_scale_pct"]),
            gross_scale_pct=float(data["gross_scale_pct"]),
            ramp_schedule=list(data.get("ramp_schedule", [])),
        )


@dataclass
class RollbackRef:
    target_artifact_id: str
    target_version: str
    action_type: RollbackActionType | str = RollbackActionType.REPLACE
    reason: Optional[str] = None
    verified_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "target_artifact_id": self.target_artifact_id,
            "target_version": self.target_version,
            "action_type": (
                self.action_type.value
                if isinstance(self.action_type, RollbackActionType)
                else self.action_type
            ),
        }
        if self.reason:
            payload["reason"] = self.reason
        if self.verified_at:
            payload["verified_at"] = self.verified_at
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RollbackRef":
        return cls(
            target_artifact_id=str(data["target_artifact_id"]),
            target_version=str(data["target_version"]),
            action_type=_normalize_rollback_action_type(
                data.get("action_type", RollbackActionType.REPLACE.value)
            ).value,
            reason=data.get("reason"),
            verified_at=data.get("verified_at"),
        )


@dataclass(frozen=True)
class DeploymentExecutionProjection:
    metadata_key: str
    artifact_key: str
    metadata: Dict[str, Any]


@dataclass
class DeploymentPlan:
    plan_id: str
    approval_decision_id: str
    artifact_id: str
    artifact_version: str
    artifact_type: str
    strategy_id: str
    capital_pool_id: str
    current_stage: DeploymentStage | str
    target_stage: DeploymentStage | str
    transition_type: TransitionType | str
    runtime_action: RuntimeAction | str
    status: PlanStatus | str
    created_at: str
    created_by: Optional[str] = None
    sponsor_persona_id: Optional[str] = None
    runtime_config_ref: Optional[str] = None
    binding_id: Optional[str] = None
    schedule_window: Optional[ScheduleWindow] = None
    scale: Optional[DeploymentScale] = None
    rollback: Optional[RollbackRef] = None
    pre_checks: List[str] = field(default_factory=list)
    post_checks: List[str] = field(default_factory=list)
    metadata: Optional[Dict[str, Any]] = None
    supersedes_plan_id: Optional[str] = None

    def validate(self) -> List[str]:
        errors: List[str] = []
        required = {
            "plan_id": self.plan_id,
            "approval_decision_id": self.approval_decision_id,
            "artifact_id": self.artifact_id,
            "artifact_version": self.artifact_version,
            "artifact_type": self.artifact_type,
            "strategy_id": self.strategy_id,
            "capital_pool_id": self.capital_pool_id,
            "created_at": self.created_at,
        }
        for field_name, value in required.items():
            if value in (None, ""):
                errors.append(f"{field_name} is required")

        try:
            current_stage = DeploymentStage(self.current_stage)
        except ValueError:
            errors.append(f"Invalid current_stage: {self.current_stage}")
            current_stage = None
        try:
            target_stage = DeploymentStage(self.target_stage)
        except ValueError:
            errors.append(f"Invalid target_stage: {self.target_stage}")
            target_stage = None
        try:
            transition_type = TransitionType(self.transition_type)
        except ValueError:
            errors.append(f"Invalid transition_type: {self.transition_type}")
            transition_type = None
        try:
            runtime_action = RuntimeAction(self.runtime_action)
        except ValueError:
            errors.append(f"Invalid runtime_action: {self.runtime_action}")
            runtime_action = None
        try:
            plan_status = PlanStatus(self.status)
        except ValueError:
            errors.append(f"Invalid status: {self.status}")
            plan_status = None

        stages_match = current_stage and target_stage and current_stage == target_stage
        if stages_match and plan_status != PlanStatus.EXECUTED:
            errors.append("target_stage must differ from current_stage")

        if current_stage and target_stage and transition_type and not stages_match:
            try:
                expected_transition = StagePlanner().derive_transition_type(current_stage, target_stage)
            except DeploymentPlanError as exc:
                errors.append(str(exc))
                expected_transition = None
            if expected_transition and transition_type != expected_transition:
                errors.append(
                    f"transition_type mismatch: expected {expected_transition.value}, got {transition_type.value}"
                )

        if transition_type and runtime_action:
            allowed_actions = {
                TransitionType.ACTIVATE: {RuntimeAction.DEPLOY_NEW_BINDING},
                TransitionType.PROMOTE: {RuntimeAction.REPLACE_BINDING},
                TransitionType.ROLLBACK: {
                    RuntimeAction.REPLACE_BINDING,
                    RuntimeAction.PAUSE_THEN_REPLACE,
                    RuntimeAction.LIQUIDATE_THEN_REPLACE,
                },
                TransitionType.FREEZE: {RuntimeAction.FREEZE_BINDING},
                TransitionType.RESUME: {RuntimeAction.RESUME_BINDING},
            }
            if runtime_action not in allowed_actions.get(transition_type, set()):
                expected_values = ", ".join(sorted(action.value for action in allowed_actions[transition_type]))
                errors.append(
                    f"runtime_action '{runtime_action.value}' not allowed for transition_type '{transition_type.value}' "
                    f"(expected one of: {expected_values})"
                )

        if target_stage in {DeploymentStage.PAPER, DeploymentStage.CANARY, DeploymentStage.LIVE}:
            if self.rollback is None:
                errors.append(f"rollback is required for target_stage '{target_stage.value}'")

        if transition_type == TransitionType.ROLLBACK and self.rollback is None:
            errors.append("rollback transition requires rollback linkage")

        if self.rollback:
            if not self.rollback.target_artifact_id:
                errors.append("rollback.target_artifact_id is required")
            if not self.rollback.target_version:
                errors.append("rollback.target_version is required")
            if self.rollback.target_artifact_id == self.artifact_id:
                errors.append("rollback.target_artifact_id cannot equal artifact_id")
            if self.rollback.target_version == self.artifact_version:
                errors.append("rollback.target_version cannot equal artifact_version")
            try:
                rollback_action = _normalize_rollback_action_type(self.rollback.action_type)
            except ValueError:
                errors.append(f"Invalid rollback.action_type: {self.rollback.action_type}")
            else:
                if rollback_action not in {
                    RollbackActionType.REPLACE,
                    RollbackActionType.PAUSE_THEN_REPLACE,
                    RollbackActionType.LIQUIDATE_THEN_REPLACE,
                }:
                    errors.append(
                        "rollback.action_type must be replace, pause_then_replace, or liquidate_then_replace"
                    )
                expected_runtime_action = _runtime_action_for_rollback_action(rollback_action)
                if transition_type == TransitionType.ROLLBACK and runtime_action and expected_runtime_action != runtime_action:
                    errors.append(
                        "runtime_action must align with rollback.action_type for rollback transitions"
                    )

        if self.scale:
            if not 0 <= self.scale.capital_scale_pct <= 100:
                errors.append("scale.capital_scale_pct must be between 0 and 100")
            if not 0 <= self.scale.gross_scale_pct <= 100:
                errors.append("scale.gross_scale_pct must be between 0 and 100")
            if target_stage == DeploymentStage.PAPER and self.scale.capital_scale_pct != 0:
                errors.append("paper target_stage requires scale.capital_scale_pct = 0")
            if target_stage == DeploymentStage.CANARY:
                if self.scale.capital_scale_pct <= 0 or self.scale.capital_scale_pct > 5:
                    errors.append("canary target_stage requires 0 < capital_scale_pct <= 5")
                if self.scale.gross_scale_pct <= 0 or self.scale.gross_scale_pct > 25:
                    errors.append("canary target_stage requires 0 < gross_scale_pct <= 25")
            if target_stage == DeploymentStage.FROZEN:
                if self.scale.capital_scale_pct != 0 or self.scale.gross_scale_pct != 0:
                    errors.append("frozen target_stage requires both scale percentages to be 0")

        if self.schedule_window:
            start_at = _parse_timestamp(self.schedule_window.start_at)
            end_at = _parse_timestamp(self.schedule_window.end_at) if self.schedule_window.end_at else None
            if start_at is None:
                errors.append("schedule_window.start_at is invalid")
            if end_at is not None and start_at is not None and end_at <= start_at:
                errors.append("schedule_window.end_at must be after start_at")

        return errors

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["current_stage"] = (
            self.current_stage.value if isinstance(self.current_stage, DeploymentStage) else self.current_stage
        )
        payload["target_stage"] = (
            self.target_stage.value if isinstance(self.target_stage, DeploymentStage) else self.target_stage
        )
        payload["transition_type"] = (
            self.transition_type.value if isinstance(self.transition_type, TransitionType) else self.transition_type
        )
        payload["runtime_action"] = (
            self.runtime_action.value if isinstance(self.runtime_action, RuntimeAction) else self.runtime_action
        )
        payload["status"] = self.status.value if isinstance(self.status, PlanStatus) else self.status
        if self.schedule_window:
            payload["schedule_window"] = self.schedule_window.to_dict()
        if self.scale:
            payload["scale"] = self.scale.to_dict()
        if self.rollback:
            payload["rollback"] = self.rollback.to_dict()
        return payload

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DeploymentPlan":
        schedule_window = data.get("schedule_window")
        scale = data.get("scale")
        rollback = data.get("rollback")
        return cls(
            plan_id=str(data["plan_id"]),
            approval_decision_id=str(data["approval_decision_id"]),
            artifact_id=str(data["artifact_id"]),
            artifact_version=str(data["artifact_version"]),
            artifact_type=str(data["artifact_type"]),
            strategy_id=str(data["strategy_id"]),
            capital_pool_id=str(data["capital_pool_id"]),
            current_stage=data["current_stage"],
            target_stage=data["target_stage"],
            transition_type=data["transition_type"],
            runtime_action=data["runtime_action"],
            status=data["status"],
            created_at=str(data["created_at"]),
            created_by=data.get("created_by"),
            sponsor_persona_id=data.get("sponsor_persona_id"),
            runtime_config_ref=data.get("runtime_config_ref"),
            binding_id=data.get("binding_id"),
            schedule_window=ScheduleWindow.from_dict(schedule_window) if isinstance(schedule_window, Mapping) else None,
            scale=DeploymentScale.from_dict(scale) if isinstance(scale, Mapping) else None,
            rollback=RollbackRef.from_dict(rollback) if isinstance(rollback, Mapping) else None,
            pre_checks=list(data.get("pre_checks", [])),
            post_checks=list(data.get("post_checks", [])),
            metadata=data.get("metadata"),
            supersedes_plan_id=data.get("supersedes_plan_id"),
        )

    @classmethod
    def from_json(cls, payload: str) -> "DeploymentPlan":
        return cls.from_dict(json.loads(payload))


class StagePlanner:
    """Plan canonical deployment-stage transitions."""

    _STAGE_ORDER = {
        DeploymentStage.NONE: 0,
        DeploymentStage.PAPER: 1,
        DeploymentStage.CANARY: 2,
        DeploymentStage.LIVE: 3,
    }

    @staticmethod
    def derive_transition_type(
        current_stage: DeploymentStage | str,
        target_stage: DeploymentStage | str,
    ) -> TransitionType:
        current = DeploymentStage(current_stage)
        target = DeploymentStage(target_stage)

        if current == target:
            raise DeploymentPlanError("current_stage and target_stage cannot be identical")
        if current == DeploymentStage.NONE and target == DeploymentStage.PAPER:
            return TransitionType.ACTIVATE
        if current in {DeploymentStage.PAPER, DeploymentStage.CANARY, DeploymentStage.LIVE} and target == DeploymentStage.FROZEN:
            return TransitionType.FREEZE
        if current == DeploymentStage.FROZEN and target in {
            DeploymentStage.PAPER,
            DeploymentStage.CANARY,
            DeploymentStage.LIVE,
        }:
            return TransitionType.RESUME

        if current == DeploymentStage.NONE:
            raise DeploymentPlanError(f"Forbidden transition: {current.value} -> {target.value}")

        if target in StagePlanner._active_stages():
            current_rank = StagePlanner._STAGE_ORDER.get(current)
            target_rank = StagePlanner._STAGE_ORDER.get(target)
            if current_rank is None or target_rank is None:
                raise DeploymentPlanError(f"Forbidden transition: {current.value} -> {target.value}")
            if target_rank == current_rank + 1:
                return TransitionType.PROMOTE
            if target_rank < current_rank:
                allowed_rollbacks = {
                    (DeploymentStage.CANARY, DeploymentStage.PAPER),
                    (DeploymentStage.LIVE, DeploymentStage.CANARY),
                    (DeploymentStage.LIVE, DeploymentStage.PAPER),
                }
                if (current, target) in allowed_rollbacks:
                    return TransitionType.ROLLBACK
            raise DeploymentPlanError(f"Forbidden transition: {current.value} -> {target.value}")

        raise DeploymentPlanError(f"Forbidden transition: {current.value} -> {target.value}")

    @staticmethod
    def _active_stages() -> set[DeploymentStage]:
        return {DeploymentStage.PAPER, DeploymentStage.CANARY, DeploymentStage.LIVE}

    def default_runtime_action(
        self,
        transition_type: TransitionType | str,
        rollback: RollbackRef | None = None,
    ) -> RuntimeAction:
        transition = TransitionType(transition_type)
        if transition == TransitionType.ACTIVATE:
            return RuntimeAction.DEPLOY_NEW_BINDING
        if transition == TransitionType.PROMOTE:
            return RuntimeAction.REPLACE_BINDING
        if transition == TransitionType.FREEZE:
            return RuntimeAction.FREEZE_BINDING
        if transition == TransitionType.RESUME:
            return RuntimeAction.RESUME_BINDING
        if rollback:
            rollback_action = _normalize_rollback_action_type(rollback.action_type)
            return _runtime_action_for_rollback_action(rollback_action)
        return RuntimeAction.REPLACE_BINDING

    def default_scale(self, target_stage: DeploymentStage | str) -> DeploymentScale:
        target = DeploymentStage(target_stage)
        if target == DeploymentStage.PAPER:
            return DeploymentScale(capital_scale_pct=0.0, gross_scale_pct=100.0)
        if target == DeploymentStage.CANARY:
            return DeploymentScale(capital_scale_pct=5.0, gross_scale_pct=25.0)
        if target == DeploymentStage.LIVE:
            return DeploymentScale(capital_scale_pct=100.0, gross_scale_pct=100.0)
        if target == DeploymentStage.FROZEN:
            return DeploymentScale(capital_scale_pct=0.0, gross_scale_pct=0.0)
        raise DeploymentPlanError(f"Unsupported target_stage for scale defaults: {target.value}")

    def create_plan(
        self,
        *,
        plan_id: str,
        approval_decision_id: str,
        registry_entry: Mapping[str, Any],
        capital_pool_id: str,
        target_stage: DeploymentStage | str,
        approval_decision: Mapping[str, Any] | None = None,
        current_stage: DeploymentStage | str | None = None,
        created_by: str | None = None,
        sponsor_persona_id: str | None = None,
        runtime_config_ref: str | None = None,
        binding_id: str | None = None,
        schedule_window: ScheduleWindow | None = None,
        scale: DeploymentScale | None = None,
        rollback: RollbackRef | None = None,
        pre_checks: list[str] | None = None,
        post_checks: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        supersedes_plan_id: str | None = None,
        status: PlanStatus | str = PlanStatus.APPROVED,
        risk_policy: RiskPolicy | Mapping[str, Any] | None = None,
        risk_policy_context: Mapping[str, Any] | None = None,
    ) -> DeploymentPlan:
        normalized_entry = self.normalize_registry_entry(registry_entry)
        artifact_state = normalized_entry["artifact_state"]
        if artifact_state != "approved":
            raise DeploymentPlanError(
                f"Deployment planning requires artifact_state=approved, got '{artifact_state}'"
            )

        resolved_current_stage = DeploymentStage(
            current_stage or self.resolve_current_stage(normalized_entry)
        )
        resolved_target_stage = DeploymentStage(target_stage)
        transition_type = self.derive_transition_type(resolved_current_stage, resolved_target_stage)
        effective_scale = scale or self.default_scale(resolved_target_stage)
        runtime_action = self.default_runtime_action(transition_type, rollback)

        if approval_decision is not None:
            self._validate_approval_decision(
                approval_decision=approval_decision,
                approval_decision_id=approval_decision_id,
                registry_entry=normalized_entry,
                capital_pool_id=capital_pool_id,
                sponsor_persona_id=sponsor_persona_id,
            )

        plan = DeploymentPlan(
            plan_id=plan_id,
            approval_decision_id=approval_decision_id,
            artifact_id=normalized_entry["registry_id"],
            artifact_version=normalized_entry["version"],
            artifact_type=normalized_entry["artifact_type"],
            strategy_id=normalized_entry["strategy_id"],
            capital_pool_id=capital_pool_id,
            current_stage=resolved_current_stage,
            target_stage=resolved_target_stage,
            transition_type=transition_type,
            runtime_action=runtime_action,
            status=status,
            created_at=utc_now(),
            created_by=created_by,
            sponsor_persona_id=sponsor_persona_id,
            runtime_config_ref=runtime_config_ref,
            binding_id=binding_id,
            schedule_window=schedule_window,
            scale=effective_scale,
            rollback=rollback,
            pre_checks=list(pre_checks or []),
            post_checks=list(post_checks or []),
            metadata=metadata,
            supersedes_plan_id=supersedes_plan_id,
        )
        errors = plan.validate()
        if errors:
            raise DeploymentPlanError("; ".join(errors))
        if risk_policy is not None:
            evaluation = self.evaluate_risk_policy(
                risk_policy=risk_policy,
                plan=plan,
                registry_entry=normalized_entry,
                extra_context=risk_policy_context,
            )
            if evaluation.rejected:
                raise DeploymentPlanError(
                    risk_policy_rejection_message("DeploymentPlan blocked", evaluation)
                )
            plan.metadata = dict(plan.metadata or {})
            plan.metadata["risk_policy_evaluation"] = evaluation.to_dict()
        return plan

    def evaluate_risk_policy(
        self,
        *,
        risk_policy: RiskPolicy | Mapping[str, Any],
        plan: DeploymentPlan,
        registry_entry: Mapping[str, Any],
        extra_context: Mapping[str, Any] | None = None,
    ) -> RiskPolicyEvaluation:
        """Evaluate a deployment plan through the shared RiskPolicy contract."""

        metadata = _mapping(plan.metadata)
        registry_metadata = _mapping(registry_entry.get("metadata"))
        overrides = _mapping(extra_context)
        scale = plan.scale
        context_payload: dict[str, Any] = {
            "target_type": RiskPolicyTargetType.DEPLOYMENT_PLAN.value,
            "target_id": plan.plan_id,
            "capital_pool_id": plan.capital_pool_id,
            "stage": _enum_value(plan.target_stage),
            "risk_policy_ref": _first_non_empty(
                overrides.get("risk_policy_ref"),
                metadata.get("risk_policy_ref"),
                registry_entry.get("risk_policy_ref"),
                registry_metadata.get("risk_policy_ref"),
            ),
            "target_weights": _first_mapping(
                overrides.get("target_weights"),
                metadata.get("target_weights"),
                registry_entry.get("target_weights"),
                registry_metadata.get("target_weights"),
            ),
            "asset_classes": _first_sequence(
                overrides.get("asset_classes"),
                metadata.get("asset_classes"),
                registry_entry.get("asset_classes"),
                registry_metadata.get("asset_classes"),
            ),
            "strategy_family": _first_non_empty(
                overrides.get("strategy_family"),
                metadata.get("strategy_family"),
                registry_entry.get("strategy_family"),
                registry_metadata.get("strategy_family"),
            ),
            "gross_exposure": _first_non_empty(
                overrides.get("gross_exposure"),
                metadata.get("gross_exposure"),
                registry_metadata.get("gross_exposure"),
            ),
            "net_exposure": _first_non_empty(
                overrides.get("net_exposure"),
                metadata.get("net_exposure"),
                registry_metadata.get("net_exposure"),
            ),
            "leverage": _first_non_empty(
                overrides.get("leverage"),
                metadata.get("leverage"),
                registry_metadata.get("leverage"),
            ),
            "turnover": _first_non_empty(
                overrides.get("turnover"),
                metadata.get("turnover"),
                registry_metadata.get("turnover"),
            ),
            "capital_scale_pct": overrides.get(
                "capital_scale_pct",
                scale.capital_scale_pct if scale is not None else None,
            ),
            "gross_scale_pct": overrides.get(
                "gross_scale_pct",
                scale.gross_scale_pct if scale is not None else None,
            ),
            "runtime_action": _enum_value(plan.runtime_action),
            "metadata": {**registry_metadata, **metadata, **overrides},
            "trace_id": _first_non_empty(overrides.get("trace_id"), metadata.get("trace_id")),
        }
        return RiskPolicyEvaluator().evaluate(
            risk_policy,
            RiskPolicyEvaluationContext.from_mapping(context_payload),
        )

    def build_execution_projection(
        self,
        plan: DeploymentPlan,
        registry_entry: Mapping[str, Any],
    ) -> DeploymentExecutionProjection:
        normalized_entry = self.normalize_registry_entry(registry_entry)
        artifact_state = normalized_entry["artifact_state"]
        if artifact_state != "approved":
            raise DeploymentPlanError("Execution projection requires artifact_state=approved")

        required = ("registry_id", "artifact_type", "strategy_id", "version", "checksum")
        missing = [field for field in required if normalized_entry.get(field) in (None, "")]
        if missing:
            raise DeploymentPlanError(
                "Execution projection requires registry fields: " + ", ".join(missing)
            )

        metadata = {
            "registry_id": normalized_entry["registry_id"],
            "strategy_id": normalized_entry["strategy_id"],
            "version": normalized_entry["version"],
            "artifact_type": normalized_entry["artifact_type"],
            "artifact_state": artifact_state,
            "deployment_stage": (
                plan.target_stage.value if isinstance(plan.target_stage, DeploymentStage) else plan.target_stage
            ),
            "approval_decision_id": plan.approval_decision_id,
            "deployment_plan_id": plan.plan_id,
            "capital_pool_id": plan.capital_pool_id,
            "runtime_action": (
                plan.runtime_action.value if isinstance(plan.runtime_action, RuntimeAction) else plan.runtime_action
            ),
            "checksum": normalized_entry["checksum"],
            "lineage": _normalize_lineage(normalized_entry.get("lineage")),
            "created_at": normalized_entry.get("created_at") or plan.created_at,
        }
        approved_at = normalized_entry.get("approved_at")
        if approved_at:
            metadata["approved_at"] = approved_at
        if plan.sponsor_persona_id:
            metadata["sponsor_persona_id"] = plan.sponsor_persona_id
        if plan.scale:
            metadata["capital_scale_pct"] = plan.scale.capital_scale_pct
            metadata["gross_scale_pct"] = plan.scale.gross_scale_pct
        if plan.rollback:
            metadata["rollback"] = {
                "target_registry_id": plan.rollback.target_artifact_id,
                "target_version": plan.rollback.target_version,
                "action_type": (
                    plan.rollback.action_type.value
                    if isinstance(plan.rollback.action_type, RollbackActionType)
                    else plan.rollback.action_type
                ),
            }
        legacy_alias = _legacy_promotion_state(plan.target_stage)
        if legacy_alias:
            metadata["promotion_state"] = legacy_alias

        metadata_key, artifact_key = self.build_object_store_keys(
            strategy_id=normalized_entry["strategy_id"],
            version=normalized_entry["version"],
        )
        return DeploymentExecutionProjection(
            metadata_key=metadata_key,
            artifact_key=artifact_key,
            metadata=metadata,
        )

    @staticmethod
    def build_object_store_keys(strategy_id: str, version: str) -> tuple[str, str]:
        base_key = f"openclaw/registry/{strategy_id}/{version}"
        return f"{base_key}/metadata.json", f"{base_key}/artifact.bin"

    def resolve_current_stage(self, registry_entry: Mapping[str, Any]) -> DeploymentStage:
        deployment_summary = registry_entry.get("deployment_summary")
        if isinstance(deployment_summary, Mapping) and deployment_summary.get("current_stage"):
            return DeploymentStage(deployment_summary["current_stage"])

        legacy_state = registry_entry.get("lifecycle_state")
        if legacy_state in {"paper", "canary", "live"}:
            return DeploymentStage(legacy_state)
        return DeploymentStage.NONE

    def normalize_registry_entry(self, registry_entry: Mapping[str, Any]) -> Dict[str, Any]:
        normalized = copy.deepcopy(dict(registry_entry))
        normalized["artifact_state"] = _resolve_artifact_state(normalized)
        if "deployment_summary" not in normalized or not isinstance(normalized["deployment_summary"], Mapping):
            current_stage = self.resolve_current_stage(normalized)
            normalized["deployment_summary"] = {"current_stage": current_stage.value}
        return normalized

    def _validate_approval_decision(
        self,
        *,
        approval_decision: Mapping[str, Any],
        approval_decision_id: str,
        registry_entry: Mapping[str, Any],
        capital_pool_id: str,
        sponsor_persona_id: str | None,
    ) -> None:
        if approval_decision.get("decision_id") != approval_decision_id:
            raise DeploymentPlanError("approval_decision_id does not match approval_decision.decision_id")
        if approval_decision.get("decision_state") != DecisionState.DECIDED.value:
            raise DeploymentPlanError("approval_decision must be in decided state")
        if approval_decision.get("decision") not in {
            DecisionOutcome.APPROVED.value,
            DecisionOutcome.APPROVED_WITH_CONDITIONS.value,
        }:
            raise DeploymentPlanError("approval_decision must be approved or approved_with_conditions")
        if approval_decision.get("target_id") != registry_entry.get("registry_id"):
            raise DeploymentPlanError("approval_decision.target_id must match registry_entry.registry_id")
        if approval_decision.get("target_version") != registry_entry.get("version"):
            raise DeploymentPlanError("approval_decision.target_version must match registry_entry.version")
        if approval_decision.get("capital_pool_id") not in (None, "", capital_pool_id):
            raise DeploymentPlanError("approval_decision.capital_pool_id must match DeploymentPlan.capital_pool_id")
        if sponsor_persona_id and approval_decision.get("persona_id") not in (None, "", sponsor_persona_id):
            raise DeploymentPlanError("approval_decision.persona_id must match sponsor_persona_id")


def _legacy_promotion_state(target_stage: DeploymentStage | str) -> str | None:
    stage = DeploymentStage(target_stage)
    if stage in {DeploymentStage.PAPER, DeploymentStage.LIVE}:
        return stage.value
    return None


def _enum_value(value: Any) -> Any:
    return value.value if isinstance(value, Enum) else value


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _first_non_empty(*values: Any) -> str | None:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _first_mapping(*values: Any) -> dict[str, Any]:
    for value in values:
        if isinstance(value, Mapping):
            return dict(value)
    return {}


def _first_sequence(*values: Any) -> list[str]:
    for value in values:
        if isinstance(value, str):
            return [value] if value else []
        if isinstance(value, (list, tuple, set)):
            return [str(item) for item in value if str(item).strip()]
    return []


def _normalize_lineage(lineage: Any) -> dict[str, Any]:
    if not isinstance(lineage, Mapping):
        return {}
    normalized = dict(lineage)
    if "source_run_ids" not in normalized and normalized.get("source_run_id"):
        normalized["source_run_ids"] = [normalized["source_run_id"]]
    return normalized


def _resolve_artifact_state(entry: Mapping[str, Any]) -> str:
    artifact_state = entry.get("artifact_state")
    if artifact_state:
        return str(artifact_state)

    legacy_state = entry.get("lifecycle_state")
    legacy_map = {
        "draft": "draft",
        "candidate": "candidate",
        "paper": "approved",
        "canary": "approved",
        "live": "approved",
        "retired": "retired",
    }
    if legacy_state in legacy_map:
        return legacy_map[legacy_state]
    raise DeploymentPlanError("registry_entry is missing artifact_state and has no mappable lifecycle_state")


def validate_plan_json(data: Mapping[str, Any]) -> List[str]:
    errors: List[str] = []
    required = [
        "plan_id",
        "approval_decision_id",
        "artifact_id",
        "artifact_version",
        "artifact_type",
        "strategy_id",
        "capital_pool_id",
        "current_stage",
        "target_stage",
        "transition_type",
        "runtime_action",
        "status",
        "created_at",
    ]
    for key in required:
        if data.get(key) in (None, ""):
            errors.append(f"Missing required field: {key}")

    enums: list[tuple[str, type[Enum]]] = [
        ("current_stage", DeploymentStage),
        ("target_stage", DeploymentStage),
        ("transition_type", TransitionType),
        ("runtime_action", RuntimeAction),
        ("status", PlanStatus),
    ]
    for field_name, enum_cls in enums:
        if data.get(field_name) in (None, ""):
            continue
        valid_values = [member.value for member in enum_cls]
        if data[field_name] not in valid_values:
            errors.append(
                f"Invalid {field_name}: {data[field_name]}. Must be one of {valid_values}"
            )

    rollback = data.get("rollback")
    target_stage = data.get("target_stage")
    if target_stage in {"paper", "canary", "live"} and not isinstance(rollback, Mapping):
        errors.append(f"rollback is required for target_stage '{target_stage}'")

    if isinstance(rollback, Mapping):
        for key in ("target_artifact_id", "target_version", "action_type"):
            if rollback.get(key) in (None, ""):
                errors.append(f"Missing required rollback field: {key}")
        if rollback.get("action_type") not in {
            RollbackActionType.REPLACE.value,
            RollbackActionType.PAUSE_THEN_REPLACE.value,
            RollbackActionType.LIQUIDATE_THEN_REPLACE.value,
        }:
            errors.append(
                "rollback.action_type must be one of replace, pause_then_replace, liquidate_then_replace"
            )

    return errors


def validate_plan(plan: DeploymentPlan) -> List[str]:
    return plan.validate()


def _normalize_rollback_action_type(
    value: RollbackActionType | str,
) -> RollbackActionType:
    if isinstance(value, RollbackActionType):
        return value
    if value == RuntimeAction.REPLACE_BINDING.value:
        return RollbackActionType.REPLACE
    return RollbackActionType(value)


def _runtime_action_for_rollback_action(
    rollback_action: RollbackActionType,
) -> RuntimeAction:
    if rollback_action == RollbackActionType.REPLACE:
        return RuntimeAction.REPLACE_BINDING
    return RuntimeAction(rollback_action.value)


class DeploymentPlanStore:
    """Simple in-memory store for DeploymentPlan objects."""

    def __init__(self, storage_path: Optional[str] = None):
        self._storage_path = Path(storage_path) if storage_path else None
        self._plans: Dict[str, DeploymentPlan] = {}
        if self._storage_path and self._storage_path.exists():
            self._load()

    def put(self, plan: DeploymentPlan) -> None:
        self._plans[plan.plan_id] = plan
        self._save()

    def get(self, plan_id: str) -> Optional[DeploymentPlan]:
        return self._plans.get(plan_id)

    def list_all(self) -> List[DeploymentPlan]:
        return list(self._plans.values())

    def find_by_pool(self, capital_pool_id: str) -> List[DeploymentPlan]:
        return [
            plan for plan in self._plans.values()
            if plan.capital_pool_id == capital_pool_id
        ]

    def _save(self) -> None:
        if not self._storage_path:
            return
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)
        raw = {plan_id: plan.to_dict() for plan_id, plan in self._plans.items()}
        self._storage_path.write_text(json.dumps(raw, indent=2), encoding="utf-8")

    def _load(self) -> None:
        if not self._storage_path:
            return
        text = self._storage_path.read_text(encoding="utf-8")
        if not text.strip():
            return
        raw = json.loads(text)
        for plan_id, payload in raw.items():
            self._plans[plan_id] = DeploymentPlan.from_dict(payload)
