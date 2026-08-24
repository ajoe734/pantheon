"""Real decision event producer projecting strategy, risk, and runtime evidence into owner-scoped decision events."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from .models import (
    DecisionEventEvidenceRef,
    DecisionEventFreshness,
    DecisionEventRecord,
    DecisionProjectionCommand,
    compute_event_digest,
)
from .store import DecisionEventStore


def _parse_iso(iso_str: str) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


class DecisionEventProducer:
    """Producer projecting owner-scoped signal/risk evidence into decision events."""

    def __init__(self, store: Optional[DecisionEventStore] = None) -> None:
        self.store = store or DecisionEventStore()

    def produce_decision_event(
        self,
        command: DecisionProjectionCommand,
        tenant_id: str,
        user_id: str,
        utc_now: Optional[str] = None,
    ) -> DecisionEventRecord:
        # 1. Producer Idempotency Check
        existing = self.store.get_by_idempotency_key(tenant_id, user_id, command.idempotency_key)
        if existing is not None:
            return existing

        now_dt = datetime.now(timezone.utc)
        now_str = utc_now or now_dt.isoformat()
        current_dt = _parse_iso(now_str) or now_dt

        # 2. Parse timestamps & check freshness
        signal_dt = _parse_iso(command.signal_as_of)
        risk_dt = _parse_iso(command.risk_as_of)

        invalidation_conditions = []
        is_fresh = True

        if signal_dt is None:
            invalidation_conditions.append("INVALID_SIGNAL_AS_OF")
            is_fresh = False
        else:
            sig_age = (current_dt - signal_dt).total_seconds()
            if sig_age < 0 or sig_age > command.max_staleness_sec:
                invalidation_conditions.append("STALE_SIGNAL_DATA")
                is_fresh = False

        if risk_dt is None:
            invalidation_conditions.append("INVALID_RISK_AS_OF")
            is_fresh = False
        else:
            risk_age = (current_dt - risk_dt).total_seconds()
            if risk_age < 0 or risk_age > command.max_staleness_sec:
                invalidation_conditions.append("STALE_RISK_DATA")
                is_fresh = False

        # 3. Risk Verification
        if not command.risk_data:
            invalidation_conditions.append("MISSING_RISK_DATA")
        elif command.risk_data.get("risk_passed") is False:
            invalidation_conditions.append("RISK_CHECK_FAILED")
        elif "max_drawdown" not in command.risk_data and "risk_score" not in command.risk_data:
            invalidation_conditions.append("INCOMPLETE_RISK_METRICS")

        # 4. Fail-closed decision logic
        if invalidation_conditions or not is_fresh:
            status = "invalidated" if invalidation_conditions else "stale"
            probability = 0.0
            expected_value = 0.0
        else:
            status = "projected"
            sig_conf = float(command.signal_data.get("confidence", 0.5))
            risk_factor = max(0.0, 1.0 - float(command.risk_data.get("risk_score", 0.1)))
            probability = max(0.0, min(1.0, round(sig_conf * risk_factor, 4)))
            expected_value = float(command.signal_data.get("expected_value", command.signal_data.get("ev", 0.0)))

        # Build deterministic or UUID event ID
        event_seed = {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "idempotency_key": command.idempotency_key,
            "strategy_id": command.strategy_id,
            "event_type": command.event_type,
        }
        event_id = f"evt-{compute_event_digest(event_seed)[:16]}"

        freshness = DecisionEventFreshness(
            evaluated_at=now_str,
            signal_as_of=command.signal_as_of,
            risk_as_of=command.risk_as_of,
            max_staleness_sec=command.max_staleness_sec,
            is_fresh=is_fresh,
        )

        record = DecisionEventRecord(
            decision_event_id=event_id,
            idempotency_key=command.idempotency_key,
            tenant_id=tenant_id,
            user_id=user_id,
            owner_scope="user_private",
            strategy_id=command.strategy_id,
            persona_id=command.persona_id,
            event_type=command.event_type,
            probability=probability,
            expected_value=expected_value,
            risk=command.risk_data,
            invalidation_conditions=invalidation_conditions,
            freshness=freshness,
            evidence_refs=command.evidence_refs,
            status=status,
            created_at=now_str,
            has_broker_authority=False,
        )

        # 5. Persist and return
        return self.store.save_event(record)

    def project_to_trading_room(
        self,
        record: DecisionEventRecord,
        *,
        trading_room_store: Optional[Any] = None,
        strategy_spec_registry_id: Optional[str] = None,
        symbol: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Project a DecisionEventRecord into a canonical TradingDecisionEvent in TradingRoomStore."""
        from ..trading_room.router import _get_store as _get_tr_store
        tr_store = trading_room_store or _get_tr_store()

        event_kind = {
            "entry": "entry",
            "add": "add",
            "reduce": "reduce",
            "exit": "exit",
            "review": "review",
        }.get(record.event_type, "entry" if record.status == "projected" else "review")

        confidence_val = min(1.0, max(0.0, float(record.probability)))
        event_dict: Dict[str, Any] = {
            "spec_version": "1.0",
            "decision_event_id": record.decision_event_id,
            "dedupe_key": record.idempotency_key,
            "event_kind": event_kind,
            "origin": (
                "strategy_signal"
                if record.event_type.startswith("signal")
                else "risk_rule"
                if record.event_type.startswith("risk")
                else "servant_analysis"
            ),
            "strategy_id": record.strategy_id,
            "strategy_spec_registry_id": strategy_spec_registry_id or record.strategy_id,
            "subject": {
                "symbol": symbol or record.strategy_id,
                "asset_class": "equity",
                "venue": "default",
            },
            "state": (
                "pending_review"
                if record.status == "projected"
                else "invalidated"
                if record.status == "invalidated"
                else "expired"
                if record.status == "stale"
                else "decided"
            ),
            "triggered_at": record.created_at,
            "confidence": {
                "value": confidence_val,
                "basis": "model",
                "calibration_state": "calibrated" if record.freshness.is_fresh else "uncalibrated",
                "sample_size": 100,
            },
            "probability": {
                "target_outcome": "positive_alpha",
                "horizon": "20d",
                "value": record.probability,
            },
            "expected_value": {
                "horizon": "20d",
                "unit": "pct_return",
                "gross": record.expected_value,
                "cost": 0.005,
                "net": max(0.0, record.expected_value - 0.005),
                "downside": 0.02,
            },
            "rationale": [
                {
                    "claim": f"Projected decision event for {record.strategy_id} from {record.event_type}",
                    "confidence": confidence_val,
                    "evidence_refs": [
                        {"ref_type": ref.ref_type, "ref_id": ref.ref_id}
                        for ref in record.evidence_refs
                    ] or [{"ref_type": "decision_event_record", "ref_id": record.decision_event_id}],
                }
            ],
            "invalidation": {
                "conditions": record.invalidation_conditions or ["price_gap_breach"],
                "current_state": "valid" if record.status == "projected" else "invalidated",
                "last_checked_at": record.freshness.evaluated_at,
            },
            "suggested_action": "enter" if record.status == "projected" else "no_action",
            "suggested_size": {
                "size_hint": "small",
                "portfolio_pct": 0.01,
                "non_binding": True,
            },
            "no_order_route_proof": "agora_decision_support_only",
        }
        tr_store.upsert_decision_event(event_dict)
        return event_dict

