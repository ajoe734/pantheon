"""AgoraInteractionEvidence models for the dataset extraction surface.

Governance boundary: evidence extraction only — no artifact promotion,
no RuntimeBinding mutation, no capital binding, no order routing.

Interaction kinds route to two dataset buckets:
  observe — passive capture: ask, journal, note, insight
  learn   — supervised signal: feedback, training_example, decision,
            rejection, deferral, intervention
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class InteractionKind(str, Enum):
    ASK = "ask"
    FEEDBACK = "feedback"
    JOURNAL = "journal"
    NOTE = "note"
    INSIGHT = "insight"
    TRAINING_EXAMPLE = "training_example"
    DECISION = "decision"
    REJECTION = "rejection"
    DEFERRAL = "deferral"
    INTERVENTION = "intervention"


class DatasetKind(str, Enum):
    OBSERVE = "observe"
    LEARN = "learn"


DECISION_EVIDENCE_KINDS: frozenset[str] = frozenset(
    {
        InteractionKind.DECISION.value,
        InteractionKind.REJECTION.value,
        InteractionKind.DEFERRAL.value,
        InteractionKind.INTERVENTION.value,
    }
)

# Retention and daily per-trader growth budget (SD §7.3, §8.3, T4):
# - One evidence item is emitted per Agora decision lock or decision journal entry.
# - Retention follows the existing 30-day dataset retention (bounded 1..365 days).
# - At ~2 KB per evidence item and up to 50 trader decisions/journal entries per day,
#   growth is ~20 KB/day typical (10 items/day) and capped at ~100 KB/day (50 items/day),
#   or <= 3 MB per trader across the 30-day retention window.
DEFAULT_DATASET_RETENTION_DAYS: int = 30
ESTIMATED_DECISION_EVIDENCE_BYTES: int = 2048
MAX_DECISION_EVIDENCE_PER_TRADER_PER_DAY: int = 50
MAX_DECISION_EVIDENCE_GROWTH_BYTES_PER_TRADER_PER_DAY: int = (
    MAX_DECISION_EVIDENCE_PER_TRADER_PER_DAY * ESTIMATED_DECISION_EVIDENCE_BYTES
)
DECISION_EVIDENCE_RETENTION_BUDGET: Dict[str, Any] = {
    "cadence": "event_driven_on_lock_or_journal_write",
    "items_per_lock_or_journal_entry": 1,
    "estimated_item_bytes": ESTIMATED_DECISION_EVIDENCE_BYTES,
    "typical_items_per_trader_per_day": 10,
    "typical_growth_bytes_per_trader_per_day": 10 * ESTIMATED_DECISION_EVIDENCE_BYTES,
    "max_items_per_trader_per_day": MAX_DECISION_EVIDENCE_PER_TRADER_PER_DAY,
    "max_growth_bytes_per_trader_per_day": MAX_DECISION_EVIDENCE_GROWTH_BYTES_PER_TRADER_PER_DAY,
    "retention_days": DEFAULT_DATASET_RETENTION_DAYS,
    "max_retention_days": 365,
}


_KIND_TO_DATASET: dict[str, DatasetKind] = {
    InteractionKind.ASK.value: DatasetKind.OBSERVE,
    InteractionKind.JOURNAL.value: DatasetKind.OBSERVE,
    InteractionKind.NOTE.value: DatasetKind.OBSERVE,
    InteractionKind.INSIGHT.value: DatasetKind.OBSERVE,
    InteractionKind.FEEDBACK.value: DatasetKind.LEARN,
    InteractionKind.TRAINING_EXAMPLE.value: DatasetKind.LEARN,
    InteractionKind.DECISION.value: DatasetKind.LEARN,
    InteractionKind.REJECTION.value: DatasetKind.LEARN,
    InteractionKind.DEFERRAL.value: DatasetKind.LEARN,
    InteractionKind.INTERVENTION.value: DatasetKind.LEARN,
}


def route_to_dataset(interaction_kind: str) -> DatasetKind:
    """Route an interaction kind to the appropriate governed dataset.

    Returns DatasetKind.OBSERVE as the safe default for unrecognised kinds.
    """
    return _KIND_TO_DATASET.get(str(interaction_kind), DatasetKind.OBSERVE)


class AgoraInteractionEvidenceRequest(BaseModel):
    """Submitted Agora interaction evidence payload.

    Fields that callers must supply:
      evidence_id      — stable caller-generated ID; used for idempotency
      interaction_kind — one of InteractionKind enum values
      persona_id       — servant persona that generated or received the evidence
      captured_at      — ISO-8601 timestamp when evidence was captured

    Optional / Governance & Privacy:
      session_id                   — servant session context when available
      content                      — interaction payload (question text, feedback label, etc.)
      source_refs                  — upstream artifact or session references
      learning_eligible            — defaults True; set False to suppress from learn pipelines
      consent_granted              — defaults True; explicit user consent for dataset extraction
      purpose                      — declared usage purpose (e.g. "policy_learning", "observe")
      retention_days               — data retention lifespan in days (default 30)
      is_raw_conversation          — set True if content includes raw conversation messages
      explicit_conversation_consent — required True to extract raw conversation data
      allowed_use                  — permitted downstream use lanes
    """

    model_config = {"extra": "forbid"}

    spec_version: Literal["1.0"] = "1.0"
    evidence_id: str = Field(min_length=1)
    interaction_kind: InteractionKind
    persona_id: str = Field(min_length=1)
    session_id: Optional[str] = None
    content: Dict[str, Any] = Field(default_factory=dict)
    source_refs: List[str] = Field(default_factory=list)
    learning_eligible: bool = True
    captured_at: str = Field(min_length=1)
    consent_granted: bool = True
    purpose: Optional[str] = "policy_learning"
    retention_days: Optional[int] = 30
    is_raw_conversation: bool = False
    explicit_conversation_consent: bool = False
    allowed_use: List[str] = Field(default_factory=lambda: ["policy_learning", "observe", "evaluation"])


class DatasetAdmissionReceipt(BaseModel):
    """Admission receipt for an interaction evidence submission (admit-only).

    Returned immediately upon durable persistence to the inbox without waiting
    for worker processing.
    """

    evidence_id: str
    tenant_id: str
    user_id: str
    interaction_kind: InteractionKind
    persona_id: str
    session_id: Optional[str] = None
    content: Dict[str, Any]
    source_refs: List[str]
    learning_eligible: bool
    governance_boundary: Literal["observe_or_learn_only"] = "observe_or_learn_only"
    no_promote_proof: Literal["agora_observe_learn_only"] = "agora_observe_learn_only"
    no_runtime_mutation_proof: Literal["agora_evidence_extract_only"] = "agora_evidence_extract_only"
    captured_at: str
    extracted_at: str
    status: Literal["pending", "processing", "processed", "failed"] = "pending"
    idempotent: bool = False
    version: int = 1
    admission_receipt_id: str
    dataset_kind: DatasetKind
    consent_granted: bool = True
    purpose: Optional[str] = "policy_learning"
    retention_days: Optional[int] = 30
    is_raw_conversation: bool = False
    explicit_conversation_consent: bool = False


class DatasetRecord(BaseModel):
    """A record stored in a governed dataset bucket (observe or learn).

    The governance proof fields are immutable literals enforced at write time.
    No code path in this module writes RuntimeBinding, capital binding, or
    broker orders.
    """

    evidence_id: str
    dataset_version_id: str
    dataset_kind: DatasetKind
    interaction_kind: InteractionKind
    persona_id: str
    session_id: Optional[str] = None
    tenant_id: str
    user_id: str
    content: Dict[str, Any]
    source_refs: List[str]
    learning_eligible: bool
    # Governance proof — invariant; carried in every persisted record
    governance_boundary: Literal["observe_or_learn_only"] = "observe_or_learn_only"
    no_promote_proof: Literal["agora_observe_learn_only"] = "agora_observe_learn_only"
    no_runtime_mutation_proof: Literal["agora_evidence_extract_only"] = "agora_evidence_extract_only"
    captured_at: str
    extracted_at: str
    # True when this response was served from an existing record (duplicate evidence_id)
    idempotent: bool = False
    version: int = 1
    status: Literal["pending", "processing", "processed", "failed"] = "processed"
    consent_verified: bool = True
    redaction_applied: bool = False
    purpose: Optional[str] = None
    retention_days: Optional[int] = None
    admission_receipt_id: Optional[str] = None


class HandoffAcknowledgementRequest(BaseModel):
    """Durable downstream acknowledgement for one dataset-version handoff.

    The expected dataset version is required so a stale downstream consumer
    cannot acknowledge a newer extraction accidentally.  Acknowledgement only
    closes the Observe/Learn handoff; it carries no runtime, deployment, order,
    or capital authority.
    """

    model_config = {"extra": "forbid"}

    acknowledgement_id: str = Field(min_length=1)
    dataset_version_id: str = Field(min_length=1)
    downstream_ref: Any = Field(description="Downstream reference string or dictionary identifier")


__all__ = [
    "AgoraInteractionEvidenceRequest",
    "DECISION_EVIDENCE_KINDS",
    "DECISION_EVIDENCE_RETENTION_BUDGET",
    "DEFAULT_DATASET_RETENTION_DAYS",
    "DatasetAdmissionReceipt",
    "DatasetKind",
    "DatasetRecord",
    "ESTIMATED_DECISION_EVIDENCE_BYTES",
    "HandoffAcknowledgementRequest",
    "InteractionKind",
    "MAX_DECISION_EVIDENCE_GROWTH_BYTES_PER_TRADER_PER_DAY",
    "MAX_DECISION_EVIDENCE_PER_TRADER_PER_DAY",
    "route_to_dataset",
]
