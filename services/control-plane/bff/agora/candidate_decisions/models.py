"""Strict command and receipt models for daily candidate decisions.

No model in this module conveys execution authority. Validation results and
formal approvals are read from canonical server-side adapters/stores; browser
commands can only carry bindings to an exact persisted candidate revision.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator


SHA256_PATTERN = r"^[a-f0-9]{64}$"


def canonical_sha256(value: Any) -> str:
    raw = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _aware(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone offset")
    return value


class CandidateFromMeasureCommand(BaseModel):
    model_config = {"extra": "forbid"}

    interaction_id: str = Field(min_length=1)
    opinion_id: str = Field(min_length=1)
    measure_id: str = Field(min_length=1)


class CandidateDecisionCommand(BaseModel):
    model_config = {"extra": "forbid"}

    action: Literal["modify", "accept_for_review", "reject", "defer", "cancel"]
    reason: str = Field(min_length=1)
    expected_revision: int = Field(ge=1)
    expected_proposal_digest: str = Field(pattern=SHA256_PATTERN)
    proposed_value: Optional[Any] = None
    evidence_refs: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def modification_has_value(self) -> "CandidateDecisionCommand":
        if self.action == "modify" and "proposed_value" not in self.model_fields_set:
            raise ValueError("modify requires proposed_value")
        return self


class AuthoritativeValidationRequest(BaseModel):
    """The complete public input surface for validation.

    There is deliberately no validation result field. The trusted adapter
    produces the result after receiving this exact server-derived binding.
    """

    model_config = {"extra": "forbid"}

    proposal_id: str = Field(min_length=1)
    revision: int = Field(ge=1)
    proposal_digest: str = Field(pattern=SHA256_PATTERN)
    validation_plan_ref: str = Field(min_length=1)


class CandidateValidationCommand(BaseModel):
    model_config = {"extra": "forbid"}

    expected_revision: int = Field(ge=1)
    expected_proposal_digest: str = Field(pattern=SHA256_PATTERN)


class FormalApprovalLinkCommand(BaseModel):
    model_config = {"extra": "forbid"}

    expected_revision: int = Field(ge=1)
    expected_proposal_digest: str = Field(pattern=SHA256_PATTERN)


class AuthoritativeValidationReceipt(BaseModel):
    model_config = {"extra": "forbid"}

    validation_receipt_id: str = Field(min_length=1)
    authority: Literal["canonical_validation_service"]
    tenant_id: str = Field(min_length=1)
    proposal_id: str = Field(min_length=1)
    revision: int = Field(ge=1)
    proposal_digest: str = Field(pattern=SHA256_PATTERN)
    outcome: Literal["passed", "failed", "inconclusive"]
    evidence_refs: list[str] = Field(min_length=1)
    validated_at: datetime
    expires_at: datetime
    receipt_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def temporal_order(self) -> "AuthoritativeValidationReceipt":
        validated = _aware(self.validated_at, "validated_at")
        expires = _aware(self.expires_at, "expires_at")
        if expires <= validated:
            raise ValueError("validation receipt must expire after validation")
        return self

    def digest_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"receipt_sha256"})

class FormalApprovalReceipt(BaseModel):
    model_config = {"extra": "forbid"}

    approval_decision_id: str = Field(min_length=1)
    authority: Literal["canonical_approval_decision_store"]
    tenant_id: str = Field(min_length=1)
    proposal_id: str = Field(min_length=1)
    revision: int = Field(ge=1)
    proposal_digest: str = Field(pattern=SHA256_PATTERN)
    validation_receipt_id: str = Field(min_length=1)
    validation_receipt_sha256: str = Field(pattern=SHA256_PATTERN)
    proposer_id: str = Field(min_length=1)
    reviewer_id: str = Field(min_length=1)
    outcome: Literal["approved", "rejected", "revision_requested"]
    self_approval: Literal[False]
    decided_at: datetime
    expires_at: datetime
    receipt_sha256: str = Field(pattern=SHA256_PATTERN)
    execution_authority: Literal["none"]

    @model_validator(mode="after")
    def distinct_current_reviewer(self) -> "FormalApprovalReceipt":
        decided = _aware(self.decided_at, "decided_at")
        expires = _aware(self.expires_at, "expires_at")
        if self.proposer_id == self.reviewer_id:
            raise ValueError("proposal self-approval is forbidden")
        if expires <= decided:
            raise ValueError("formal approval must expire after it is decided")
        return self

    def digest_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"receipt_sha256"})
