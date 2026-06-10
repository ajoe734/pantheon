"""
REG-002: Pantheon promotion gate and EX-001 execution projection helpers.
"""
from __future__ import annotations

import copy
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, Optional


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class PromotionState(str, Enum):
    DRAFT = "draft"
    CANDIDATE = "candidate"
    PAPER = "paper"
    CANARY = "canary"
    LIVE = "live"
    RETIRED = "retired"


class PromotionError(ValueError):
    """Raised when a promotion requirement is not met."""


@dataclass(frozen=True)
class ExecutionProjection:
    metadata_key: str
    artifact_key: str
    metadata: dict[str, Any]


_LIFECYCLE_TO_ARTIFACT_STATE: dict[str, str] = {
    "candidate": "candidate",
    "paper": "approved",
    "canary": "approved",
    "live": "approved",
    "retired": "retired",
}

_LIFECYCLE_TO_DEPLOYMENT_STAGE: dict[str, str] = {
    "candidate": "none",
    "paper": "paper",
    "canary": "canary",
    "live": "live",
    "retired": "none",
}


class PromotionGate:
    def __init__(self, logger: logging.Logger | None = None):
        self.log = logger or logging.getLogger(__name__)

    @staticmethod
    def build_object_store_keys(strategy_id: str, version: str) -> tuple[str, str]:
        base_key = f"openclaw/registry/{strategy_id}/{version}"
        return f"{base_key}/metadata.json", f"{base_key}/artifact.bin"

    def validate_transition(self, current_state: PromotionState, target_state: PromotionState) -> None:
        """Enforces allowed transitions per REG-001 and PAPER_CANARY_LIVE_POLICY canonical path."""
        allowed = {
            PromotionState.DRAFT: [PromotionState.CANDIDATE, PromotionState.RETIRED],
            PromotionState.CANDIDATE: [PromotionState.PAPER, PromotionState.RETIRED],
            PromotionState.PAPER: [PromotionState.CANARY, PromotionState.RETIRED],
            PromotionState.CANARY: [PromotionState.LIVE, PromotionState.RETIRED],
            PromotionState.LIVE: [PromotionState.RETIRED],
        }

        if target_state not in allowed.get(current_state, []):
            raise PromotionError(f"Forbidden transition: {current_state.value} -> {target_state.value}")

    def check_requirements(self, target_state: PromotionState, metadata: Mapping[str, Any]) -> None:
        """Enforces metadata requirements for each promotion state."""
        lineage = _normalize_lineage(metadata.get("lineage"))

        if target_state == PromotionState.CANDIDATE:
            if not metadata.get("replication_success"):
                raise PromotionError("Candidate promotion requires 'replication_success' flag.")
            if not _has_source_reference(lineage):
                raise PromotionError(
                    "Candidate promotion requires lineage with source_run_ids, "
                    "source_strategy_spec_id, or source_dataset_refs."
                )

        elif target_state == PromotionState.PAPER:
            eval_summary = metadata.get("evaluation_summary", {})
            if not isinstance(eval_summary, Mapping):
                raise PromotionError("Paper promotion requires an evaluation_summary object.")
            if not eval_summary.get("risk_review_passed"):
                raise PromotionError("Paper promotion requires 'risk_review_passed' in evaluation_summary.")
            if eval_summary.get("sharpe_ratio") in (None, ""):
                raise PromotionError("Paper promotion requires Sharpe Ratio metric.")
            if not _has_source_reference(lineage):
                raise PromotionError("Paper promotion requires lineage with at least one source reference.")

        elif target_state == PromotionState.CANARY:
            eval_summary = metadata.get("evaluation_summary", {})
            if not isinstance(eval_summary, Mapping):
                raise PromotionError("Canary promotion requires an evaluation_summary object.")
            if not eval_summary.get("risk_review_passed"):
                raise PromotionError("Canary promotion requires 'risk_review_passed' in evaluation_summary.")
            if eval_summary.get("sharpe_ratio") in (None, ""):
                raise PromotionError("Canary promotion requires Sharpe Ratio metric.")
            if not metadata.get("approver"):
                raise PromotionError("Canary promotion requires an explicit 'approver'.")
            if not _has_source_reference(lineage):
                raise PromotionError("Canary promotion requires lineage with at least one source reference.")
            rollback = _extract_rollback(metadata)
            if rollback is None:
                raise PromotionError(
                    "Canary promotion requires metadata.rollback or "
                    "metadata.rollback_target_registry_id plus rollback_target."
                )
            _validate_rollback(
                rollback,
                current_registry_id=metadata.get("registry_id"),
                current_version=metadata.get("version"),
            )

        elif target_state == PromotionState.LIVE:
            if not metadata.get("approver"):
                raise PromotionError("Live promotion requires an explicit 'approver'.")
            if not _has_source_reference(lineage):
                raise PromotionError("Live promotion requires lineage with at least one source reference.")

            rollback = _extract_rollback(metadata)
            if rollback is None:
                raise PromotionError(
                    "Live promotion requires metadata.rollback or "
                    "metadata.rollback_target_registry_id plus rollback_target."
                )
            _validate_rollback(
                rollback,
                current_registry_id=metadata.get("registry_id"),
                current_version=metadata.get("version"),
            )

    def promote(
        self,
        entry: Mapping[str, Any],
        target_state: PromotionState,
        approver: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Attempts to promote a registry entry and returns the updated entry if successful.
        """
        updated_entry = copy.deepcopy(dict(entry))
        current_state = PromotionState(updated_entry.get("lifecycle_state", "draft"))

        self.log.info(
            "Attempting promotion: %s@%s (%s -> %s)",
            updated_entry.get("strategy_id"),
            updated_entry.get("version"),
            current_state.value,
            target_state.value,
        )

        self.validate_transition(current_state, target_state)
        if approver:
            updated_entry["approver"] = approver

        self.check_requirements(target_state, updated_entry)

        updated_entry["lifecycle_state"] = target_state.value
        updated_entry["promoted_at"] = utc_now()
        self.log.info(
            "Promotion successful: %s is now %s",
            updated_entry.get("strategy_id"),
            target_state.value,
        )
        return updated_entry

    def build_execution_projection(self, entry: Mapping[str, Any]) -> ExecutionProjection:
        """
        Materialize the EX-001 metadata envelope and canonical Object Store keys.
        """
        normalized = copy.deepcopy(dict(entry))
        state = normalized.get("lifecycle_state")
        if state in (None, "", "draft"):
            raise PromotionError("Execution projection requires a promoted entry, not draft state.")

        required = ("registry_id", "artifact_type", "strategy_id", "version", "checksum")
        missing = [field for field in required if normalized.get(field) in (None, "")]
        if missing:
            raise PromotionError(
                "Execution projection requires registry fields: " + ", ".join(missing)
            )

        lineage = _normalize_lineage(normalized.get("lineage"))
        if not isinstance(lineage, dict):
            raise PromotionError("Execution projection requires a lineage object.")

        artifact_state = _LIFECYCLE_TO_ARTIFACT_STATE.get(state, state)
        deployment_stage = _LIFECYCLE_TO_DEPLOYMENT_STAGE.get(state, "none")
        metadata = {
            "registry_id": normalized["registry_id"],
            "strategy_id": normalized["strategy_id"],
            "version": normalized["version"],
            "artifact_type": normalized["artifact_type"],
            "artifact_state": artifact_state,
            "deployment_stage": deployment_stage,
            "promotion_state": state,
            "checksum": normalized["checksum"],
            "lineage": lineage,
            "created_at": normalized.get("created_at") or normalized.get("promoted_at") or utc_now(),
        }

        if normalized.get("promoted_at"):
            metadata["approved_at"] = normalized["promoted_at"]
        if normalized.get("approver"):
            metadata["approver"] = normalized["approver"]

        rollback = _extract_rollback(normalized)
        if rollback is not None:
            _validate_rollback(
                rollback,
                current_registry_id=normalized.get("registry_id"),
                current_version=normalized.get("version"),
            )
            metadata["rollback"] = rollback
        if state in (PromotionState.CANARY.value, PromotionState.LIVE.value) and rollback is None:
            raise PromotionError("Canary and live execution projections require explicit rollback metadata.")

        metadata_key, artifact_key = self.build_object_store_keys(
            strategy_id=normalized["strategy_id"],
            version=normalized["version"],
        )
        return ExecutionProjection(
            metadata_key=metadata_key,
            artifact_key=artifact_key,
            metadata=metadata,
        )


def _normalize_lineage(lineage: Any) -> dict[str, Any]:
    if not isinstance(lineage, Mapping):
        return {}

    normalized = dict(lineage)
    if "source_run_ids" not in normalized and normalized.get("source_run_id"):
        normalized["source_run_ids"] = [normalized["source_run_id"]]
    return normalized


def _has_source_reference(lineage: Mapping[str, Any]) -> bool:
    return bool(
        lineage.get("source_run_ids")
        or lineage.get("source_strategy_spec_id")
        or lineage.get("source_dataset_refs")
    )


def _extract_rollback(entry: Mapping[str, Any]) -> dict[str, Any] | None:
    metadata = entry.get("metadata")
    if isinstance(metadata, Mapping):
        rollback = metadata.get("rollback")
        if isinstance(rollback, Mapping):
            required = ("target_registry_id", "target_version")
            missing = [field for field in required if rollback.get(field) in (None, "")]
            if missing:
                raise PromotionError(
                    "metadata.rollback missing required fields: " + ", ".join(missing)
                )
            return dict(rollback)

        registry_id = metadata.get("rollback_target_registry_id")
        version = entry.get("rollback_target")
        if registry_id and version:
            rollback_payload = {
                "target_registry_id": registry_id,
                "target_version": version,
            }
            reason = metadata.get("rollback_reason")
            verified_at = metadata.get("rollback_verified_at")
            if reason:
                rollback_payload["reason"] = reason
            if verified_at:
                rollback_payload["verified_at"] = verified_at
            return rollback_payload

    return None


def _validate_rollback(
    rollback: Mapping[str, Any],
    *,
    current_registry_id: Any,
    current_version: Any,
) -> None:
    if rollback.get("target_registry_id") in (None, ""):
        raise PromotionError("Rollback metadata requires target_registry_id.")
    if rollback.get("target_version") in (None, ""):
        raise PromotionError("Rollback metadata requires target_version.")
    if current_registry_id and rollback.get("target_registry_id") == current_registry_id:
        raise PromotionError("Rollback target_registry_id cannot equal the current registry_id.")
    if current_version and rollback.get("target_version") == current_version:
        raise PromotionError("Rollback target_version cannot equal the current version.")
