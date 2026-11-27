"""Agora shadow mode: immutable decision lock construction (design A4, §12.3).

Pure functions only. A decision lock records, at the moment a trader decides on a
Trading Room decision event, what the trader chose and what every other arm
(base strategy, private servant, institutional view) said, sharing one decision
time and one data cutoff. Nothing here trades, moves capital, computes an
attribution number, or calls a language model. Settlement belongs to the
attribution task. Missing arms are recorded as unavailable with a reason.

Identity, account, broker ids and raw text are kept as recorded (operator
decision, 2026-11-16); the lock is readable by operators and by that trader.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Iterable, List, Optional

LOCK_SCHEMA = "AgoraHumanDecisionLock.v1"
LOCK_RESOURCE = "AgoraDecisionLock"

EXECUTION_OBSERVATION_KINDS = (
    "verified_broker_fill",
    "imported_broker_fill",
    "pantheon_paper_proxy",
    "manual_declared_outcome",
    "declared_no_trade",
)
# Kinds the decision route may accept from a trader at lock time. A verified or
# imported broker fill needs a governed integration / checksum review that the
# decision route cannot perform, so it fails closed here.
TRADER_DECLARABLE_KINDS = (
    "pantheon_paper_proxy",
    "manual_declared_outcome",
    "declared_no_trade",
)

# A4 §1 display names. Proxy is never shown as an actual fill.
DISPLAY_LABELS = {
    "verified_broker_fill": "交易員實際成交（已驗證）",
    "imported_broker_fill": "交易員成交（匯入／待完整驗證）",
    "pantheon_paper_proxy": "交易員決策（紙上代理）",
    "manual_declared_outcome": "交易員手動回報結果",
    "declared_no_trade": "交易員選擇不交易",
}
# A4 §6 default confidence at lock time (coordinator proposals).
DEFAULT_CONFIDENCE = {
    "verified_broker_fill": 1.0,
    "imported_broker_fill": 0.8,
    "pantheon_paper_proxy": 0.75,
    "manual_declared_outcome": 0.4,
    "declared_no_trade": 1.0,
}
ACTUAL_FILL_KINDS = ("verified_broker_fill", "imported_broker_fill")

_EVENT_KIND_TO_ACTION = {
    "entry": "enter",
    "add": "add",
    "reduce": "reduce",
    "exit": "exit",
    "review": "hold",
}

DECISION_EVIDENCE_KINDS = (
    "decision",
    "rejection",
    "deferral",
    "intervention",
)

VERDICT_TO_EVIDENCE_KIND = {
    "approve": "decision",
    "reject": "rejection",
    "defer": "deferral",
    "shadow": "deferral",
    "modify": "intervention",
}


class DecisionLockExistsError(ValueError):
    """A lock already exists for this decision; locks are immutable."""

    def __init__(self, decision_event_id: str) -> None:
        super().__init__(f"decision lock already exists for {decision_event_id!r}")
        self.decision_event_id = decision_event_id
        self.status_code = 409
        self.error_code = "DECISION_LOCK_EXISTS"


class DecisionLockInvalidError(ValueError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status_code = 422
        self.error_code = "DECISION_LOCK_INVALID"


def unavailable(reason: str) -> Dict[str, Any]:
    return {"available": False, "unavailable_reason": reason}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def a4_decision(body_decision: str, event_kind: str) -> str:
    """Map the Trading Room verdict to the A4 decision vocabulary."""
    if body_decision in ("approve", "modify"):
        return _EVENT_KIND_TO_ACTION.get(event_kind, "hold")
    if body_decision == "reject":
        return "reject"
    if body_decision == "defer":
        return "defer"
    if body_decision == "shadow":
        return "no_trade"
    raise DecisionLockInvalidError(f"unsupported_decision:{body_decision}")


def decision_evidence_kind(
    body_decision: str,
    modifications: Optional[Dict[str, Any]] = None,
) -> str:
    """Map a Trading Room verdict and modifications to one of the four learning evidence kinds."""
    if body_decision == "modify" or (body_decision in ("approve", "shadow") and bool(modifications)):
        return "intervention"
    if body_decision == "reject":
        return "rejection"
    if body_decision in ("defer", "shadow"):
        return "deferral"
    if body_decision == "approve":
        return "decision"
    raise DecisionLockInvalidError(f"unsupported_decision:{body_decision}")


def build_decision_time_snapshot(
    event: Dict[str, Any],
    *,
    decision_time: str,
    data_cutoff: Optional[str] = None,
) -> Dict[str, Any]:
    """Capture the deterministic decision-time market and signal snapshot from a decision event."""
    subject = event.get("subject") if isinstance(event.get("subject"), dict) else {}
    raw_snap = (
        event.get("decision_time_snapshot")
        if isinstance(event.get("decision_time_snapshot"), dict)
        else (
            event.get("market_snapshot")
            if isinstance(event.get("market_snapshot"), dict)
            else (event.get("snapshot") if isinstance(event.get("snapshot"), dict) else {})
        )
    )
    cutoff = (
        _clean(data_cutoff)
        or _clean(event.get("data_cutoff"))
        or _clean(raw_snap.get("available_data_cutoff"))
        or None
    )
    symbol = (
        _clean(subject.get("symbol"))
        or _clean(event.get("target_instrument"))
        or _clean(raw_snap.get("symbol"))
        or None
    )
    snapshot: Dict[str, Any] = {
        **json.loads(json.dumps(raw_snap, default=str)),
        "decision_time": decision_time,
        "available_data_cutoff": cutoff,
        "triggered_at": _clean(event.get("triggered_at")) or raw_snap.get("triggered_at"),
        "symbol": symbol,
        "subject": json.loads(json.dumps(subject, default=str)) if subject else raw_snap.get("subject", {}),
        "strategy_id": _clean(event.get("strategy_id")) or raw_snap.get("strategy_id"),
        "strategy_version_id": (
            _clean(event.get("strategy_version_id") or event.get("strategy_version"))
            or raw_snap.get("strategy_version_id")
        ),
        "strategy_spec_registry_id": (
            _clean(event.get("strategy_spec_registry_id"))
            or raw_snap.get("strategy_spec_registry_id")
        ),
        "event_kind": _clean(event.get("event_kind")) or raw_snap.get("event_kind"),
        "origin": _clean(event.get("origin")) or raw_snap.get("origin"),
        "suggested_action": _clean(event.get("suggested_action")) or raw_snap.get("suggested_action"),
        "suggested_size": (
            event.get("suggested_size")
            if event.get("suggested_size") is not None
            else raw_snap.get("suggested_size")
        ),
        "confidence": (
            json.loads(json.dumps(event["confidence"], default=str))
            if isinstance(event.get("confidence"), dict)
            else event.get("confidence", raw_snap.get("confidence"))
        ),
        "probability": (
            json.loads(json.dumps(event["probability"], default=str))
            if isinstance(event.get("probability"), dict)
            else event.get("probability", raw_snap.get("probability"))
        ),
        "expected_value": (
            json.loads(json.dumps(event["expected_value"], default=str))
            if isinstance(event.get("expected_value"), dict)
            else event.get("expected_value", raw_snap.get("expected_value"))
        ),
    }
    return snapshot


def resolve_observation_kind(body_decision: str, declared: Optional[str]) -> str:
    if declared:
        if declared not in TRADER_DECLARABLE_KINDS:
            raise DecisionLockInvalidError(
                f"execution_observation_kind_not_declarable:{declared}"
            )
        if body_decision in ("reject", "defer", "shadow") and declared != "declared_no_trade":
            raise DecisionLockInvalidError("no_trade_decision_requires_declared_no_trade")
        return declared
    # Default: approve/modify map to the paper proxy path (A4 §3 item 3); the
    # lock itself carries no fill. reject/defer/shadow produce no order.
    return "pantheon_paper_proxy" if body_decision in ("approve", "modify") else "declared_no_trade"


def _arm_base_strategy(event: Dict[str, Any]) -> Dict[str, Any]:
    strategy_id = _clean(event.get("strategy_id"))
    if not strategy_id:
        return unavailable("event_has_no_strategy_id")
    version = _clean(
        event.get("strategy_version_id") or event.get("strategy_version")
    )
    arm: Dict[str, Any] = {
        "available": True,
        "strategy_id": strategy_id,
        "strategy_spec_registry_id": _clean(event.get("strategy_spec_registry_id")) or None,
        "suggested_action": event.get("suggested_action"),
        "suggested_size": event.get("suggested_size"),
        "origin": event.get("origin"),
    }
    if version:
        arm["strategy_version_id"] = version
    else:
        arm["strategy_version_id"] = None
        arm["strategy_version_unavailable_reason"] = "event_has_no_strategy_version"
    return arm


def _arm_from_event_key(event: Dict[str, Any], keys: Iterable[str], reason: str) -> Dict[str, Any]:
    for key in keys:
        value = event.get(key)
        if isinstance(value, dict) and value:
            return {"available": True, "source_field": key, **json.loads(json.dumps(value))}
    return unavailable(reason)


def canonical_checksum(lock: Dict[str, Any]) -> str:
    body = {k: v for k, v in lock.items() if k != "checksum"}
    encoded = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def build_decision_lock(
    *,
    human_decision_id: str,
    decision_record_id: str,
    event: Dict[str, Any],
    decision_event_id: str,
    tenant_id: str,
    user_id: str,
    body_decision: str,
    rationale: Optional[str],
    modifications: Optional[Dict[str, Any]],
    decision_time: str,
    execution_observation_kind: Optional[str] = None,
    account_id: Optional[str] = None,
    broker_ids: Optional[List[str]] = None,
    actor_roles: Optional[List[str]] = None,
) -> Dict[str, Any]:
    if not _clean(tenant_id) or not _clean(user_id):
        raise DecisionLockInvalidError("trader_identity_missing")
    event_kind = _clean(event.get("event_kind"))
    kind = resolve_observation_kind(body_decision, execution_observation_kind)
    cutoff = _clean(event.get("data_cutoff"))
    subject = event.get("subject") if isinstance(event.get("subject"), dict) else {}
    acct = _clean(account_id) or _clean(event.get("account_id")) or None
    brokers = [str(b) for b in (broker_ids or event.get("broker_ids") or []) if _clean(b)]
    lock: Dict[str, Any] = {
        "schema": LOCK_SCHEMA,
        "human_decision_id": human_decision_id,
        "decision_record_id": decision_record_id,
        "decision_event_id": decision_event_id,
        "tenant_id": _clean(tenant_id),
        "user_id": _clean(user_id),
        "trader_identity": {
            "tenant_id": _clean(tenant_id),
            "user_id": _clean(user_id),
            "roles": list(actor_roles or []),
        },
        "account_id": acct,
        "account_unavailable_reason": None if acct else "account_not_supplied",
        "broker_ids": brokers,
        "broker_ids_unavailable_reason": None if brokers else "broker_ids_not_supplied",
        "strategy_id": _clean(event.get("strategy_id")) or None,
        "source_trading_event_id": decision_event_id,
        "decision_time": decision_time,
        "available_data_cutoff": cutoff or None,
        "data_cutoff_unavailable_reason": None if cutoff else "event_has_no_data_cutoff",
        "decision": a4_decision(body_decision, event_kind),
        "trading_room_verdict": body_decision,
        "evidence_kind": decision_evidence_kind(body_decision, modifications),
        "decision_time_snapshot": build_decision_time_snapshot(
            event,
            decision_time=decision_time,
            data_cutoff=cutoff or None,
        ),
        "target_instrument": _clean(subject.get("symbol")) or None,
        "event_kind": event_kind or None,
        "raw_rationale": rationale,
        "raw_modifications": modifications,
        "execution_observation_kind": kind,
        "execution_display_label": DISPLAY_LABELS[kind],
        "execution_confidence": DEFAULT_CONFIDENCE[kind],
        "execution_verified": False,
        "actual_fill_claimed": False,
        "fill_refs": [],
        "outcome": unavailable("settlement_owned_by_attribution_task"),
        "arms": {
            "human_actual": {
                "available": True,
                "action": a4_decision(body_decision, event_kind),
                "decision": a4_decision(body_decision, event_kind),
                "trading_room_verdict": body_decision,
                "execution_observation_kind": kind,
                "execution_display_label": DISPLAY_LABELS[kind],
                "execution_confidence": DEFAULT_CONFIDENCE[kind],
                "execution_verified": False,
                "actual_fill_claimed": False,
            },
            "base_strategy": _arm_base_strategy(event),
            "private_servant": _arm_from_event_key(
                event,
                ("servant_suggestion", "private_servant_suggestion"),
                "no_servant_suggestion_on_decision_event",
            ),
            "institutional_view": _arm_from_event_key(
                event,
                ("institutional_view", "committee_variant"),
                "no_institutional_view_on_decision_event",
            ),
        },
        "no_order_route_proof": "agora_decision_lock_record_only",
        "immutable": True,
    }
    lock["checksum"] = canonical_checksum(lock)
    return lock


def verify_checksum(lock: Dict[str, Any]) -> bool:
    return lock.get("checksum") == canonical_checksum(lock)


__all__ = [
    "ACTUAL_FILL_KINDS",
    "DECISION_EVIDENCE_KINDS",
    "DISPLAY_LABELS",
    "DecisionLockExistsError",
    "DecisionLockInvalidError",
    "EXECUTION_OBSERVATION_KINDS",
    "LOCK_RESOURCE",
    "LOCK_SCHEMA",
    "TRADER_DECLARABLE_KINDS",
    "VERDICT_TO_EVIDENCE_KIND",
    "a4_decision",
    "build_decision_lock",
    "build_decision_time_snapshot",
    "canonical_checksum",
    "decision_evidence_kind",
    "unavailable",
    "verify_checksum",
]
