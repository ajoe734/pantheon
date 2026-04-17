"""
Dataclass models for the feedback service event families.

Two families (contract: services/feedback/schema/contract.md):
  - TraderFeedbackEvent  — explicit human approve/edit/reject/rationale
  - ExecutionTelemetryEvent — pnl, drawdown, slippage, fills, order outcomes

Both share GovernedLinkage as the `target` object.
Draft artifacts are excluded from ExecutionTelemetryEvent by contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


def _validate_rfc3339(value: str, field_name: str = "created_at") -> None:
    """Raise ValueError if *value* is not a valid RFC3339 / ISO-8601 timestamp."""
    try:
        normalized = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            raise ValueError("missing timezone")
    except (ValueError, AttributeError) as exc:
        raise ValueError(
            f"{field_name} must be a valid RFC3339 timestamp; got {value!r}"
        ) from exc


class FeedbackEventType(str, Enum):
    APPROVE = "approve"
    EDIT = "edit"
    REJECT = "reject"
    RATIONALE = "rationale"


class TelemetryEventType(str, Enum):
    PNL_SNAPSHOT = "pnl_snapshot"
    DRAWDOWN_SNAPSHOT = "drawdown_snapshot"
    SLIPPAGE_OBSERVATION = "slippage_observation"
    FILL_OBSERVATION = "fill_observation"
    ORDER_REJECTION = "order_rejection"


class ActorRole(str, Enum):
    OPERATOR = "operator"
    APPROVER = "approver"
    SYSTEM = "system"


class Channel(str, Enum):
    CONSOLE = "console"
    WEB = "web"
    TELEGRAM = "telegram"
    DISCORD = "discord"
    API = "api"


class ArtifactType(str, Enum):
    STRATEGY_SPEC = "strategy_spec"
    MODEL_ARTIFACT = "model_artifact"
    FEATURE_SET = "feature_set"
    PROMPT_BUNDLE = "prompt_bundle"
    SIGNAL_SNAPSHOT = "signal_snapshot"
    EXECUTION_BUNDLE = "execution_bundle"


class PromotionState(str, Enum):
    DRAFT = "draft"
    CANDIDATE = "candidate"
    PAPER = "paper"
    LIVE = "live"
    RETIRED = "retired"


class ExecutionMode(str, Enum):
    PAPER = "paper"
    LIVE = "live"


class EditOperation(str, Enum):
    REPLACE = "replace"
    APPEND = "append"
    REMOVE = "remove"
    ANNOTATE = "annotate"


@dataclass
class EditItem:
    path: str
    operation: EditOperation
    value: Any = None


@dataclass
class GovernedLinkage:
    """
    Shared linkage object joining events to governed registry artifacts.
    At least one of registry_id, (artifact_version + artifact_type), or
    (lineage_ref + artifact_type) must be provided.
    """

    strategy_id: str
    registry_id: Optional[str] = None
    artifact_version: Optional[str] = None
    artifact_type: Optional[ArtifactType] = None
    promotion_state: Optional[PromotionState] = None
    lineage_ref: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.strategy_id:
            raise ValueError("strategy_id is required")
        has_registry = self.registry_id is not None
        has_version_type = (
            self.artifact_version is not None and self.artifact_type is not None
        )
        has_lineage_type = (
            self.lineage_ref is not None and self.artifact_type is not None
        )
        if not (has_registry or has_version_type or has_lineage_type):
            raise ValueError(
                "GovernedLinkage requires at least one of: registry_id, "
                "(artifact_version + artifact_type), or (lineage_ref + artifact_type)"
            )


@dataclass
class TraderFeedbackEvent:
    """Captures explicit human judgment on a governed artifact."""

    event_id: str
    event_type: FeedbackEventType
    created_at: str  # RFC3339
    actor_id: str
    actor_role: ActorRole
    channel: Channel
    target: GovernedLinkage
    task_ref: Optional[str] = None
    decision_ref: Optional[str] = None
    rationale: Optional[str] = None
    edits: List[EditItem] = field(default_factory=list)

    def __post_init__(self) -> None:
        _validate_rfc3339(self.created_at)
        if self.event_type == FeedbackEventType.EDIT and not self.edits:
            raise ValueError("edits must be non-empty for event_type='edit'")
        if self.event_type == FeedbackEventType.RATIONALE and not self.rationale:
            raise ValueError("rationale must be set for event_type='rationale'")


@dataclass
class ExecutionTelemetryEvent:
    """
    Captures how a governed artifact behaved after approval.
    Draft artifacts are excluded — telemetry begins at candidate/paper/live.
    """

    event_id: str
    event_type: TelemetryEventType
    created_at: str  # RFC3339
    execution_mode: ExecutionMode
    target: GovernedLinkage
    metrics: Dict[str, Any]
    broker: Optional[str] = None
    account_ref: Optional[str] = None
    signal_id: Optional[str] = None
    run_id: Optional[str] = None

    def __post_init__(self) -> None:
        _validate_rfc3339(self.created_at)
        if not self.metrics:
            raise ValueError("metrics must contain at least one entry")
        if (
            self.target.promotion_state is not None
            and self.target.promotion_state == PromotionState.DRAFT
        ):
            raise ValueError(
                "Draft artifacts must not appear in execution telemetry. "
                "Telemetry begins at candidate/paper/live."
            )
