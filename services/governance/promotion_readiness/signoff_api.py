"""Create/read/append-signature API for promotion human gate signoff."""
from __future__ import annotations

import copy
import json
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

from services.governance.human_gate.decision_model import (
    CanProceedInput,
    EvidenceReviewed,
    HumanGateDecision,
    HumanGateDecisionError,
    HumanGateSignature,
    compute_evidence_hash,
    stable_hash,
    utc_now,
    validate_decision,
)
from services.governance.promotion_readiness.packet_model import (
    PASSING_STATUSES,
    PromotionReadinessPacket,
)


class SignoffApiError(HumanGateDecisionError):
    """Raised when the signoff API cannot complete an operation."""


class HumanGateDecisionStore:
    """Small JSON-backed store for HumanGateDecision records."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path is not None else None
        self._items: dict[str, HumanGateDecision] = {}
        if self.path and self.path.exists():
            self._load()

    def _load(self) -> None:
        if self.path is None:
            return
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise SignoffApiError(f"{self.path} must contain a JSON object")
        records = payload.get("decisions", payload)
        if not isinstance(records, Mapping):
            raise SignoffApiError(f"{self.path} decisions must be an object")
        self._items = {
            decision_id: HumanGateDecision.from_dict(record)
            for decision_id, record in records.items()
        }

    def _save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "decisions": {
                decision_id: decision.to_dict()
                for decision_id, decision in sorted(self._items.items())
            }
        }
        self.path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def create(self, decision: HumanGateDecision) -> HumanGateDecision:
        if decision.decision_id in self._items:
            raise SignoffApiError(f"decision already exists: {decision.decision_id}")
        normalized = validate_decision(decision)
        self._items[normalized.decision_id] = normalized
        self._save()
        return normalized

    def put(self, decision: HumanGateDecision) -> HumanGateDecision:
        normalized = validate_decision(decision)
        self._items[normalized.decision_id] = normalized
        self._save()
        return normalized

    def get(self, decision_id: str) -> HumanGateDecision | None:
        return self._items.get(decision_id)

    def require(self, decision_id: str) -> HumanGateDecision:
        decision = self.get(decision_id)
        if decision is None:
            raise SignoffApiError(f"decision not found: {decision_id}")
        return decision


class SignoffAPI:
    """Task-local API facade for promotion readiness signoff records."""

    def __init__(self, store: HumanGateDecisionStore | None = None):
        self.store = store or HumanGateDecisionStore()

    def create_decision(self, payload: Mapping[str, Any] | HumanGateDecision) -> HumanGateDecision:
        decision = payload if isinstance(payload, HumanGateDecision) else HumanGateDecision.from_dict(payload)
        now = utc_now()
        if decision.created_at is None or decision.updated_at is None:
            decision = replace(
                decision,
                created_at=decision.created_at or now,
                updated_at=decision.updated_at or now,
            )
        return self.store.create(validate_decision(decision))

    def create_decision_from_packet(
        self,
        packet_or_data: PromotionReadinessPacket | Mapping[str, Any],
        *,
        decision_id: str,
        required_roles: tuple[str, ...] | list[str],
        readiness_packet_ref: str | None = None,
        target_type: str | None = None,
        target_id: str | None = None,
        target_environment: str | None = None,
    ) -> HumanGateDecision:
        packet = (
            packet_or_data
            if isinstance(packet_or_data, PromotionReadinessPacket)
            else PromotionReadinessPacket.from_dict(packet_or_data)
        )
        evidence = evidence_reviewed_from_packet(packet)
        now = utc_now()
        decision = HumanGateDecision(
            decision_id=decision_id,
            target_type=target_type or packet.target.target_type,
            target_id=target_id or packet.target.target_id,
            target_environment=target_environment or packet.target.environment,
            required_roles=tuple(required_roles),
            evidence_reviewed=evidence,
            evidence_hash=compute_evidence_hash(evidence),
            can_proceed_input=can_proceed_input_from_packet(
                packet,
                readiness_packet_ref=readiness_packet_ref,
            ),
            created_at=now,
            updated_at=now,
        )
        return self.store.create(validate_decision(decision))

    def read_decision(self, decision_id: str) -> HumanGateDecision:
        return self.store.require(decision_id)

    def append_signature(
        self,
        decision_id: str,
        signature_payload: Mapping[str, Any] | HumanGateSignature,
    ) -> HumanGateDecision:
        decision = self.store.require(decision_id)
        payload = (
            signature_payload.to_dict()
            if isinstance(signature_payload, HumanGateSignature)
            else copy.deepcopy(dict(signature_payload))
        )
        payload.setdefault("signature_id", f"hgs-{uuid.uuid4().hex[:12]}")
        payload.setdefault("signed_at", utc_now())
        payload.setdefault("evidence_hash", decision.evidence_hash)
        payload.setdefault("evidence_reviewed", [item.key for item in decision.evidence_reviewed])
        payload.setdefault("meaning", "approved")
        signature = HumanGateSignature.from_dict(payload)
        updated = decision.with_signature(signature)
        return self.store.put(updated)


def evidence_reviewed_from_packet(packet: PromotionReadinessPacket) -> tuple[EvidenceReviewed, ...]:
    """Build reviewed evidence entries from a readiness packet's provided evidence."""

    entries: list[EvidenceReviewed] = []
    for item in packet.evidence.provided:
        item_payload = item.to_dict()
        evidence_hash = item_payload.get("evidence_hash")
        if not evidence_hash:
            evidence_hash = stable_hash(item_payload)
        source_ref = (
            item.path
            or item.source_task_id
            or item.ref_type
            or item.key
        )
        entries.append(
            EvidenceReviewed(
                key=item.key,
                evidence_hash=str(evidence_hash),
                source_ref=source_ref,
                status=item.status,
                metadata={
                    key: value
                    for key, value in item_payload.items()
                    if key not in {"key", "evidence_hash", "source_ref", "status"}
                },
            )
        )
    return tuple(entries)


def can_proceed_input_from_packet(
    packet: PromotionReadinessPacket,
    *,
    readiness_packet_ref: str | None = None,
) -> CanProceedInput:
    blocking_gate_results = tuple(
        f"{item.gate}:{item.status}"
        for item in packet.evidence.gate_results
        if item.status not in PASSING_STATUSES
    )
    return CanProceedInput(
        readiness_packet_ref=readiness_packet_ref or packet.packet_id,
        readiness_packet_can_proceed=packet.can_proceed,
        required_evidence=packet.evidence.required,
        missing_evidence=packet.evidence.missing,
        blocking_reasons=tuple(item.to_a2_1_value() for item in packet.blocking_reasons),
        unsafe_true_flags=packet.flags.unsafe_true_flags(),
        gate_results_blocking=blocking_gate_results,
    )


_DEFAULT_API = SignoffAPI()


def create_decision(payload: Mapping[str, Any] | HumanGateDecision) -> HumanGateDecision:
    return _DEFAULT_API.create_decision(payload)


def read_decision(decision_id: str) -> HumanGateDecision:
    return _DEFAULT_API.read_decision(decision_id)


def append_signature(
    decision_id: str,
    signature_payload: Mapping[str, Any] | HumanGateSignature,
) -> HumanGateDecision:
    return _DEFAULT_API.append_signature(decision_id, signature_payload)
