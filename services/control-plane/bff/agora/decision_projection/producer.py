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
