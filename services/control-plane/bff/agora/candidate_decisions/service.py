"""Application service for daily decisions and canonical review linkage."""
from __future__ import annotations

import copy
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Protocol

from .models import (
    AuthoritativeValidationReceipt,
    AuthoritativeValidationRequest,
    CandidateDecisionCommand,
    CandidateFromMeasureCommand,
    FormalApprovalReceipt,
    canonical_sha256,
)
from .source import build_candidate_from_persisted_measure, candidate_digest
from .store import CandidateDecisionConflict, CandidateDecisionStore, StoredMutation
from ..interaction.store import InteractionLifecycleStore


class CanonicalValidationAdapter(Protocol):
    adapter_id: str

    def readiness(self, *, candidate: Mapping[str, Any]) -> Mapping[str, Any]: ...

    def validate(
        self,
        request: AuthoritativeValidationRequest,
        *,
        validation_plan: Mapping[str, Any],
        candidate: Mapping[str, Any],
    ) -> AuthoritativeValidationReceipt | Mapping[str, Any]: ...


class CanonicalApprovalStore(Protocol):
    def readiness(self) -> Mapping[str, Any]: ...

    def get_formal_approval(self, approval_decision_id: str) -> Mapping[str, Any] | None: ...


_ACTION_TO_RECORD = {
    "modify": "modified",
    "accept_for_review": "accepted_for_review",
    "reject": "rejected",
    "defer": "deferred",
    "cancel": "cancelled",
}

_ACTION_TO_STATE = {
    "modify": "draft",
    "accept_for_review": "review_requested",
    "reject": "rejected",
    "defer": "deferred",
    "cancel": "cancelled",
}

_TERMINAL_STATES = frozenset({"approved", "rejected", "cancelled"})
_ALLOWED_DAILY_ACTIONS = {
    "draft": frozenset({"modify", "accept_for_review", "reject", "defer", "cancel"}),
    "review_requested": frozenset({"modify", "reject", "defer", "cancel"}),
    "deferred": frozenset({"modify", "accept_for_review", "reject", "cancel"}),
}


def _parse_time(value: Any, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise CandidateDecisionConflict(f"{field} is not a valid timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CandidateDecisionConflict(f"{field} must include a timezone offset")
    return parsed


class CandidateDecisionService:
    def __init__(
        self,
        store: CandidateDecisionStore,
        *,
        interaction_store: InteractionLifecycleStore,
        validation_adapter: CanonicalValidationAdapter,
        approval_store: CanonicalApprovalStore,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.store = store
        self.interaction_store = interaction_store
        self.validation_adapter = validation_adapter
        self.approval_store = approval_store
        self.clock = clock

    def readback(
        self,
        *,
        proposal_id: str,
        tenant_id: str,
        owner_user_id: str,
    ) -> dict[str, Any]:
        """Return reload-safe canonical candidate history and receipt arrays."""
        current = self._current(proposal_id, tenant_id, owner_user_id)
        validations = self.store.validation_receipts(
            proposal_id, tenant_id, owner_user_id
        )
        approvals = self.store.approval_receipts(
            proposal_id, tenant_id, owner_user_id
        )
        readiness = self._readiness(current, validations, approvals)
        return {
            "candidate": current,
            "revisions": self.store.history(proposal_id, tenant_id, owner_user_id),
            "decisions": self.store.decisions(proposal_id, tenant_id, owner_user_id),
            "validation_receipts": validations,
            "formal_approval_receipts": approvals,
            "etag": self.store.etag(current),
            "readiness": readiness,
            "execution_authority": "none",
        }

    def create_from_measure(
        self,
        *,
        command: CandidateFromMeasureCommand,
        tenant_id: str,
        owner_user_id: str,
        proposer_id: str,
        expires_at: datetime,
        idempotency_key: str,
    ) -> StoredMutation:
        interaction = self.interaction_store.get(
            command.interaction_id, tenant_id, owner_user_id
        )
        if interaction is None:
            raise CandidateDecisionConflict(
                "interaction was not found in the requested tenant and user scope"
            )
        now = self.clock()
        record = build_candidate_from_persisted_measure(
            interaction=interaction,
            command=command,
            tenant_id=tenant_id,
            owner_user_id=owner_user_id,
            proposer_id=proposer_id,
            now=now,
            expires_at=expires_at,
        )
        # Fingerprint only the stable request/scope identity. ``expires_at`` is
        # server-authored on every HTTP attempt, so including it would turn an
        # otherwise identical network retry into an idempotency conflict.
        fingerprint = canonical_sha256(
            {
                "command": command.model_dump(mode="json"),
                "tenant_id": tenant_id,
                "owner_user_id": owner_user_id,
                "proposer_id": proposer_id,
            }
        )
        link = {
            "proposal_id": record["proposal_id"],
            "interaction_id": record["interaction_id"],
            "opinion_id": record["opinion_id"],
            "opinion_sha256": record["opinion_sha256"],
            "measure_id": record["measure_id"],
            "measure_sha256": record["measure_sha256"],
            "proposal_digest": record["proposal_digest"],
            "revision": record["revision"],
            "state": record["state"],
            "created_at": record["created_at"],
            "execution_authority": "none",
        }
        event_id = "candidate-" + canonical_sha256(link)[:24]
        workshop_event = {
            "event_id": event_id,
            "workshop_id": interaction["workshop_id"],
            "actor_type": "operator",
            "event_type": "candidate_created",
            "private_content_ref": f"agora-candidate://{record['proposal_id']}",
            "redacted_summary": "A Persona recommended measure became a governed review candidate.",
            "payload_refs_json": {
                "spec_version": "1.9",
                **link,
                "authority": record["authority"],
            },
        }
        sse = {
            "workshop_id": interaction["workshop_id"],
            "event_type": "candidate.created",
            "data": {"event_id": event_id, **link},
        }
        return self.store.create_candidate(
            record,
            idempotency_key=idempotency_key,
            fingerprint=fingerprint,
            interaction_store=self.interaction_store,
            candidate_link=link,
            workshop_outbox=[
                {
                    "outbox_id": f"outbox:{event_id}:event",
                    "projection_kind": "workshop_event",
                    "payload": workshop_event,
                },
                {
                    "outbox_id": f"outbox:{event_id}:sse",
                    "projection_kind": "workshop_sse",
                    "payload": sse,
                },
            ],
        )

    def decide(
        self,
        *,
        proposal_id: str,
        command: CandidateDecisionCommand,
        tenant_id: str,
        owner_user_id: str,
        actor_id: str,
        expected_etag: str,
        idempotency_key: str,
    ) -> StoredMutation:
        if not actor_id:
            raise CandidateDecisionConflict("decision actor is required")
        fingerprint = canonical_sha256(
            {
                "proposal_id": proposal_id,
                "command": command.model_dump(mode="json"),
                "tenant_id": tenant_id,
                "owner_user_id": owner_user_id,
                "actor_id": actor_id,
            }
        )
        replay = self.store.replay(
            tenant_id=tenant_id,
            owner_user_id=owner_user_id,
            operation="decision",
            idempotency_key=idempotency_key,
            fingerprint=fingerprint,
        )
        if replay:
            return replay
        current = self._current(proposal_id, tenant_id, owner_user_id)
        self._assert_current_binding(current, command.expected_revision, command.expected_proposal_digest)
        if current["state"] in _TERMINAL_STATES:
            raise CandidateDecisionConflict("candidate is terminal")
        if command.action not in _ALLOWED_DAILY_ACTIONS.get(current["state"], frozenset()):
            raise CandidateDecisionConflict("candidate action is invalid for the current state")
        now = self.clock()
        if _parse_time(current["expires_at"], "expires_at") <= now:
            raise CandidateDecisionConflict("candidate is expired")

        next_record = copy.deepcopy(current)
        next_record["revision"] = current["revision"] + 1
        next_record["state"] = _ACTION_TO_STATE[command.action]
        next_record["updated_at"] = now.isoformat()
        if command.action == "modify":
            next_record["proposed_value"] = copy.deepcopy(command.proposed_value)
        audit_ref = f"audit_{uuid.uuid4().hex}"
        review_request_id = (
            f"review_{uuid.uuid4().hex}" if command.action == "accept_for_review" else None
        )
        audit = {
            "audit_ref": audit_ref,
            "action": command.action,
            "actor_id": actor_id,
            "reason": command.reason,
            "at": now.isoformat(),
            "from_revision": current["revision"],
            "from_proposal_digest": current["proposal_digest"],
            "evidence_refs": list(command.evidence_refs),
            "review_request_id": review_request_id,
            "formal_approval": False,
            "execution_authority": "none",
        }
        next_record["audit"] = list(current.get("audit") or []) + [audit]
        next_record["proposal_digest"] = candidate_digest(next_record)
        decision = {
            "decision_id": f"decision_{uuid.uuid4().hex}",
            "proposal_id": proposal_id,
            "interaction_id": current["interaction_id"],
            "opinion_id": current["opinion_id"],
            "opinion_sha256": current["opinion_sha256"],
            "measure_id": current["measure_id"],
            "measure_sha256": current["measure_sha256"],
            "action": _ACTION_TO_RECORD[command.action],
            "actor_id": actor_id,
            "reason": command.reason,
            "revision": next_record["revision"],
            "proposal_digest": next_record["proposal_digest"],
            "review_request_id": review_request_id,
            "decided_at": now.isoformat(),
            "formal_approval": False,
            "execution_authority": "none",
            "audit_ref": audit_ref,
        }
        return self.store.append_decision(
            current=current,
            expected_etag=expected_etag,
            next_record=next_record,
            decision=decision,
            idempotency_key=idempotency_key,
            fingerprint=fingerprint,
        )

    def run_authoritative_validation(
        self,
        *,
        proposal_id: str,
        tenant_id: str,
        owner_user_id: str,
        expected_revision: int,
        expected_proposal_digest: str,
        expected_etag: str,
        idempotency_key: str,
    ) -> StoredMutation:
        adapter = self.validation_adapter
        adapter_id = str(getattr(adapter, "adapter_id", "")).strip()
        preflight_request = AuthoritativeValidationRequest(
            proposal_id=proposal_id,
            revision=expected_revision,
            proposal_digest=expected_proposal_digest,
            validation_plan_ref="pending-server-binding",
        )
        # The actual plan ref is included below after loading canonical state;
        # this early replay key intentionally contains no browser result.
        preflight_fingerprint = canonical_sha256(
            {
                "proposal_id": preflight_request.proposal_id,
                "revision": preflight_request.revision,
                "proposal_digest": preflight_request.proposal_digest,
                "adapter_id": adapter_id,
                "tenant_id": tenant_id,
                "owner_user_id": owner_user_id,
            }
        )
        replay = self.store.replay(
            tenant_id=tenant_id,
            owner_user_id=owner_user_id,
            operation="validation",
            idempotency_key=idempotency_key,
            fingerprint=preflight_fingerprint,
        )
        if replay:
            return replay
        current = self._current(proposal_id, tenant_id, owner_user_id)
        self._assert_current_binding(current, expected_revision, expected_proposal_digest)
        if current["state"] != "review_requested":
            raise CandidateDecisionConflict("validation requires accept-for-review state")
        now = self.clock()
        proposal_expiry = _parse_time(current["expires_at"], "expires_at")
        if proposal_expiry <= now:
            raise CandidateDecisionConflict("candidate is expired")
        adapter_readiness = dict(adapter.readiness(candidate=current))
        if adapter_readiness.get("ready") is not True:
            raise CandidateDecisionConflict("authoritative validation adapter is not ready")
        plan_ref = f"sha256:{canonical_sha256(current['validation_plan'])}"
        request = AuthoritativeValidationRequest(
            proposal_id=proposal_id,
            revision=current["revision"],
            proposal_digest=current["proposal_digest"],
            validation_plan_ref=plan_ref,
        )
        raw_receipt = adapter.validate(
            request,
            validation_plan=copy.deepcopy(current["validation_plan"]),
            candidate=copy.deepcopy(current),
        )
        raw_receipt = (
            raw_receipt.model_dump(mode="json")
            if isinstance(raw_receipt, AuthoritativeValidationReceipt)
            else dict(raw_receipt)
        )
        supplied_checksum = raw_receipt.get("receipt_sha256")
        if supplied_checksum != canonical_sha256(
            {key: value for key, value in raw_receipt.items() if key != "receipt_sha256"}
        ):
            raise CandidateDecisionConflict("validation receipt checksum mismatch")
        receipt = AuthoritativeValidationReceipt.model_validate(raw_receipt)
        self._verify_validation_receipt(
            receipt,
            current=current,
            now=self.clock(),
            proposal_expiry=proposal_expiry,
        )
        receipt_dict = receipt.model_dump(mode="json")
        return self.store.record_validation(
            current=current,
            expected_etag=expected_etag,
            receipt=receipt_dict,
            idempotency_key=idempotency_key,
            fingerprint=preflight_fingerprint,
        )

    def link_formal_approval(
        self,
        *,
        proposal_id: str,
        approval_decision_id: str,
        tenant_id: str,
        owner_user_id: str,
        expected_revision: int,
        expected_proposal_digest: str,
        expected_etag: str,
        idempotency_key: str,
    ) -> StoredMutation:
        approval_store = self.approval_store
        fingerprint = canonical_sha256(
            {
                "proposal_id": proposal_id,
                "approval_decision_id": approval_decision_id,
                "revision": expected_revision,
                "proposal_digest": expected_proposal_digest,
                "tenant_id": tenant_id,
                "owner_user_id": owner_user_id,
            }
        )
        replay = self.store.replay(
            tenant_id=tenant_id,
            owner_user_id=owner_user_id,
            operation="approval",
            idempotency_key=idempotency_key,
            fingerprint=fingerprint,
        )
        if replay:
            return replay
        current = self._current(proposal_id, tenant_id, owner_user_id)
        self._assert_current_binding(current, expected_revision, expected_proposal_digest)
        now = self.clock()
        proposal_expiry = _parse_time(current["expires_at"], "expires_at")
        if proposal_expiry <= now:
            raise CandidateDecisionConflict("candidate is expired")
        validations = self.store.validation_receipts(proposal_id, tenant_id, owner_user_id)
        passed = [
            row for row in validations
            if row.get("outcome") == "passed"
            and row.get("revision") == current["revision"]
            and row.get("proposal_digest") == current["proposal_digest"]
            and _parse_time(row.get("expires_at"), "validation expires_at") > now
        ]
        if not passed:
            raise CandidateDecisionConflict("a current authoritative passed validation is required")
        validation = passed[-1]
        try:
            if dict(approval_store.readiness()).get("ready") is not True:
                raise CandidateDecisionConflict("canonical approval store is unavailable")
            raw = approval_store.get_formal_approval(approval_decision_id)
        except Exception as exc:
            raise CandidateDecisionConflict("canonical approval store is unavailable") from exc
        if raw is None:
            raise CandidateDecisionConflict("formal approval is not canonical")
        raw = dict(raw)
        if raw.get("revoked_at") or raw.get("superseded_by") or str(
            raw.get("state") or raw.get("decision_state") or ""
        ).strip().lower() == "revoked":
            raise CandidateDecisionConflict("formal approval is revoked or superseded")
        raw = {
            key: value for key, value in raw.items()
            if key not in {"revoked_at", "superseded_by", "state", "decision_state"}
        }
        supplied_checksum = raw.get("receipt_sha256")
        if supplied_checksum != canonical_sha256(
            {key: value for key, value in raw.items() if key != "receipt_sha256"}
        ):
            raise CandidateDecisionConflict("formal approval checksum mismatch")
        receipt = FormalApprovalReceipt.model_validate(raw)
        self._verify_formal_approval(
            receipt,
            current=current,
            validation=validation,
            now=now,
            proposal_expiry=proposal_expiry,
        )
        receipt_dict = receipt.model_dump(mode="json")
        return self.store.record_approval(
            current=current,
            expected_etag=expected_etag,
            receipt=receipt_dict,
            idempotency_key=idempotency_key,
            fingerprint=fingerprint,
        )

    def _readiness(
        self,
        current: Mapping[str, Any],
        validations: list[dict[str, Any]],
        approvals: list[dict[str, Any]],
    ) -> dict[str, Any]:
        now = self.clock()
        expired = _parse_time(current["expires_at"], "expires_at") <= now
        allowed = sorted(
            _ALLOWED_DAILY_ACTIONS.get(str(current.get("state")), frozenset())
        ) if not expired else []
        try:
            validation_status = dict(self.validation_adapter.readiness(candidate=current))
        except Exception:
            validation_status = {"ready": False, "reason": "validation_adapter_unavailable"}
        try:
            reviewer_status = dict(self.approval_store.readiness())
        except Exception:
            reviewer_status = {"ready": False, "reason": "approval_store_unavailable"}
        current_passed = next((
            row for row in reversed(validations)
            if row.get("revision") == current.get("revision")
            and row.get("proposal_digest") == current.get("proposal_digest")
            and row.get("outcome") == "passed"
            and _parse_time(row.get("expires_at"), "validation expires_at") > now
        ), None)
        current_approval = next((
            row for row in reversed(approvals)
            if row.get("revision") == current.get("revision")
            and row.get("proposal_digest") == current.get("proposal_digest")
            and row.get("outcome") == "approved"
            and _parse_time(row.get("expires_at"), "approval expires_at") > now
        ), None)
        return {
            "candidate": {
                "ready": not expired and current.get("state") not in _TERMINAL_STATES,
                "reason": "candidate_expired" if expired else None,
                "allowed_actions": allowed,
            },
            "validation": {
                "adapter_ready": validation_status.get("ready") is True,
                "reason": validation_status.get("reason"),
                "adapter_id": str(getattr(self.validation_adapter, "adapter_id", "")),
                "can_run": (
                    current.get("state") == "review_requested"
                    and not expired
                    and validation_status.get("ready") is True
                ),
                "current_passed": current_passed is not None,
                "current_receipt_id": (
                    current_passed.get("validation_receipt_id") if current_passed else None
                ),
            },
            "reviewer": {
                "store_ready": reviewer_status.get("ready") is True,
                "reason": reviewer_status.get("reason"),
                "can_request_decision": (
                    current.get("state") == "review_requested"
                    and current_passed is not None
                    and reviewer_status.get("ready") is True
                    and not expired
                ),
                "can_link_formal_approval": (
                    current_passed is not None
                    and reviewer_status.get("ready") is True
                    and current_approval is None
                    and not expired
                ),
                "current_formal_approval_id": (
                    current_approval.get("approval_decision_id") if current_approval else None
                ),
            },
            "execution_authority": "none",
        }

    def _current(self, proposal_id: str, tenant_id: str, owner_user_id: str) -> dict[str, Any]:
        current = self.store.get(proposal_id, tenant_id, owner_user_id)
        if current is None:
            raise CandidateDecisionConflict("candidate was not found in the requested scope")
        return current

    @staticmethod
    def _assert_current_binding(current: Mapping[str, Any], revision: int, digest: str) -> None:
        if current.get("revision") != revision:
            raise CandidateDecisionConflict("candidate revision is stale")
        if current.get("proposal_digest") != digest:
            raise CandidateDecisionConflict("candidate digest is stale")

    @staticmethod
    def _verify_validation_receipt(
        receipt: AuthoritativeValidationReceipt,
        *,
        current: Mapping[str, Any],
        now: datetime,
        proposal_expiry: datetime,
    ) -> None:
        if receipt.tenant_id != current["tenant_id"]:
            raise CandidateDecisionConflict("validation receipt tenant mismatch")
        if receipt.proposal_id != current["proposal_id"]:
            raise CandidateDecisionConflict("validation receipt proposal mismatch")
        if receipt.revision != current["revision"]:
            raise CandidateDecisionConflict("validation receipt revision mismatch")
        if receipt.proposal_digest != current["proposal_digest"]:
            raise CandidateDecisionConflict("validation receipt digest mismatch")
        if receipt.validated_at > now:
            raise CandidateDecisionConflict("validation receipt is future-dated")
        if receipt.expires_at <= now or receipt.expires_at > proposal_expiry:
            raise CandidateDecisionConflict("validation receipt expiry is invalid")

    @staticmethod
    def _verify_formal_approval(
        receipt: FormalApprovalReceipt,
        *,
        current: Mapping[str, Any],
        validation: Mapping[str, Any],
        now: datetime,
        proposal_expiry: datetime,
    ) -> None:
        if receipt.tenant_id != current["tenant_id"]:
            raise CandidateDecisionConflict("formal approval tenant mismatch")
        if receipt.proposal_id != current["proposal_id"]:
            raise CandidateDecisionConflict("formal approval proposal mismatch")
        if receipt.revision != current["revision"]:
            raise CandidateDecisionConflict("formal approval revision mismatch")
        if receipt.proposal_digest != current["proposal_digest"]:
            raise CandidateDecisionConflict("formal approval digest mismatch")
        if receipt.proposer_id != current["proposer_id"]:
            raise CandidateDecisionConflict("formal approval proposer mismatch")
        if receipt.reviewer_id == current["proposer_id"]:
            raise CandidateDecisionConflict("proposal self-approval is forbidden")
        if receipt.validation_receipt_id != validation["validation_receipt_id"]:
            raise CandidateDecisionConflict("formal approval validation receipt mismatch")
        if receipt.validation_receipt_sha256 != validation["receipt_sha256"]:
            raise CandidateDecisionConflict("formal approval validation checksum mismatch")
        if receipt.decided_at < _parse_time(validation["validated_at"], "validated_at"):
            raise CandidateDecisionConflict("formal approval predates validation")
        if receipt.decided_at > now:
            raise CandidateDecisionConflict("formal approval is future-dated")
        if receipt.expires_at <= now or receipt.expires_at > proposal_expiry:
            raise CandidateDecisionConflict("formal approval expiry is invalid")
