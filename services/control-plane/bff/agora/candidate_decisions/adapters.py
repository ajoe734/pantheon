"""Server-owned canonical candidate validation and approval readback adapters."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping
import uuid

from ..interaction.provider import authority_boundary
from .models import AuthoritativeValidationRequest, canonical_sha256


CANONICAL_VALIDATOR_ID = "pantheon_candidate_validation_v1"
SUPPORTED_CHECKS = frozenset({
    "source_binding",
    "evidence_freshness",
    "target_version",
    "authority_boundary",
    "rollback_plan",
})


def _parse_time(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include timezone")
    return parsed


class CandidateBindingValidationAdapter:
    """Canonical validator for provenance, freshness and no-authority binding.

    It does not claim trading-performance validation. Persona measures must
    request this exact validator/check vocabulary; unknown checks fail closed
    as not ready instead of being silently treated as passed.
    """

    adapter_id = CANONICAL_VALIDATOR_ID

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.clock = clock

    def readiness(self, *, candidate: Mapping[str, Any]) -> Mapping[str, Any]:
        plan = candidate.get("validation_plan")
        validator = str((plan or {}).get("validator") or "") if isinstance(plan, Mapping) else ""
        checks = (
            {str(item) for item in (plan or {}).get("required_checks") or []}
            if isinstance(plan, Mapping) else set()
        )
        if validator != self.adapter_id:
            return {"ready": False, "reason": "candidate_validator_not_registered"}
        if not checks or not checks.issubset(SUPPORTED_CHECKS):
            return {"ready": False, "reason": "candidate_validation_checks_unsupported"}
        return {"ready": True, "reason": None}

    def validate(
        self,
        request: AuthoritativeValidationRequest,
        *,
        validation_plan: Mapping[str, Any],
        candidate: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        readiness = self.readiness(candidate=candidate)
        if readiness.get("ready") is not True:
            raise RuntimeError(str(readiness.get("reason") or "validator_not_ready"))
        if (
            request.proposal_id != candidate.get("proposal_id")
            or request.revision != candidate.get("revision")
            or request.proposal_digest != candidate.get("proposal_digest")
        ):
            raise RuntimeError("candidate validation request binding mismatch")
        now = self.clock()
        evidence = list((candidate.get("source_measure") or {}).get("evidence_refs") or [])
        fresh = bool(evidence)
        evidence_refs: list[str] = []
        for item in evidence:
            if not isinstance(item, Mapping):
                fresh = False
                continue
            try:
                cutoff = _parse_time(item.get("data_cutoff"))
                observed = _parse_time(item.get("observed_at"))
            except (TypeError, ValueError):
                fresh = False
                continue
            if item.get("freshness") != "fresh" or cutoff > now or observed > now:
                fresh = False
            evidence_refs.append(
                "evidence:" + ":".join((
                    str(item.get("ref_type") or ""),
                    str(item.get("ref_id") or ""),
                    str(item.get("version") or "unversioned"),
                ))
            )
        binding_passed = all((
            candidate.get("authority") == authority_boundary(),
            candidate.get("execution_authority") == "none",
            bool(candidate.get("target_id")),
            bool(candidate.get("target_version")),
            bool(candidate.get("rollback_trigger")),
            bool(candidate.get("rollback_action")),
            fresh,
        ))
        expires_at = min(
            _parse_time(candidate["expires_at"]),
            now + timedelta(hours=24),
        )
        payload = {
            "validation_receipt_id": "validation_" + uuid.uuid4().hex,
            "authority": "canonical_validation_service",
            "tenant_id": str(candidate["tenant_id"]),
            "proposal_id": request.proposal_id,
            "revision": request.revision,
            "proposal_digest": request.proposal_digest,
            "outcome": "passed" if binding_passed else "failed",
            "evidence_refs": [
                f"candidate:{request.proposal_id}@{request.revision}:{request.proposal_digest}",
                *evidence_refs,
            ],
            "validated_at": now.isoformat(),
            "expires_at": expires_at.isoformat(),
        }
        payload["receipt_sha256"] = canonical_sha256(payload)
        return payload


class ReadStoreApprovalAdapter:
    """Exact read-only bridge to the canonical ApprovalDecision store."""

    def __init__(self, read_store: Callable[[], Any]) -> None:
        self._read_store = read_store

    def readiness(self) -> Mapping[str, Any]:
        try:
            getter = getattr(
                self._read_store(), "get_canonical_approval_decision_readback", None
            )
        except Exception:
            return {"ready": False, "reason": "approval_store_unavailable"}
        if not callable(getter):
            return {"ready": False, "reason": "approval_store_readback_missing"}
        try:
            probe = getter("__candidate_readiness_probe__")
            available = isinstance(probe, Mapping) and probe.get("available") is True
        except Exception:
            available = False
        return {
            "ready": available,
            "reason": None if available else "approval_store_unavailable",
        }

    def get_formal_approval(self, approval_decision_id: str) -> Mapping[str, Any] | None:
        getter = getattr(
            self._read_store(), "get_canonical_approval_decision_readback", None
        )
        if not callable(getter):
            raise RuntimeError("canonical ApprovalDecision readback is unavailable")
        readback = getter(approval_decision_id)
        if not isinstance(readback, Mapping) or readback.get("available") is not True:
            raise RuntimeError("canonical ApprovalDecision store is unavailable")
        value = readback.get("record")
        return value if isinstance(value, Mapping) else None
