"""Canonical evaluation and telemetry outcome consumer for Agora performance suggestions.

Implements SD §6.4:
  Attach `PerformanceSuggestionProducer` to the canonical evaluation/telemetry
  consumer that owns the input event. Do not add a new scheduler. The consumer
  persists the suggestion in the selected performance store and emits its
  read-model event. The BFF only queries it.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

from .models import AdjustmentSuggestion
from .producer import PerformanceOutcomeEvaluationInput, PerformanceSuggestionProducer
from .store import PerformanceSuggestionStore

logger = logging.getLogger(__name__)


def canonical_performance_publisher(
    topic: str,
    entity_id: str,
    payload: Dict[str, Any],
) -> None:
    """Canonical publisher for Agora performance events."""
    logger.info("Published canonical performance event: topic=%s entity_id=%s", topic, entity_id)


def consume_telemetry_outcome(
    event: Dict[str, Any],
    *,
    store: Optional[PerformanceSuggestionStore] = None,
    producer: Optional[PerformanceSuggestionProducer] = None,
    publish_event_fn: Optional[Callable[[str, str, Dict[str, Any]], None]] = None,
    utc_now: Optional[str] = None,
) -> AdjustmentSuggestion:
    """Consume an evaluation, paper runtime, or telemetry outcome event.

    Naturally triggers PerformanceSuggestionProducer to persist an AdjustmentSuggestion
    without introducing any new background scheduler.
    """
    prod = producer or PerformanceSuggestionProducer(store=store)

    tenant_id = str(
        event.get("tenant_id")
        or event.get("scope", {}).get("tenant_id")
        or "default-tenant"
    )
    owner_user_id = str(
        event.get("owner_user_id")
        or event.get("user_id")
        or event.get("operator_id")
        or event.get("scope", {}).get("user_id")
        or "default-user"
    )
    strategy_id = str(event.get("strategy_id") or "")
    if not strategy_id:
        raise ValueError("Telemetry outcome event must carry non-empty strategy_id")

    outcome_type = str(
        event.get("outcome_type")
        or event.get("event_type")
        or event.get("trigger_type")
        or "execution_drift"
    )
    valid_outcome_types = {
        "drawdown_breach",
        "execution_drift",
        "slippage_anomaly",
        "sharpe_degradation",
        "regime_shift",
        "turnover_excess",
    }
    if outcome_type not in valid_outcome_types:
        outcome_type = "execution_drift"

    period = event.get("period", "latest")
    if period not in {"latest", "7d", "30d", "all"}:
        period = "latest"

    correlation_id = str(
        event.get("correlation_id")
        or event.get("trace_id")
        or event.get("lineage", {}).get("correlation_id")
        or event.get("lineage", {}).get("trace_id")
        or ""
    )

    title = str(
        event.get("title")
        or f"Telemetry Triggered Adjustment: {outcome_type.replace('_', ' ').title()}"
    )
    rationale = str(
        event.get("rationale")
        or event.get("summary")
        or f"Telemetry outcome {outcome_type} breached threshold for strategy {strategy_id}"
    )

    source_id = str(event.get("source_id") or "telemetry-pipeline-v1")
    source_type = str(event.get("source_type") or "telemetry_engine")
    source_version = event.get("source_version")
    evidence_refs: List[str] = list(event.get("evidence_refs") or [])
    metrics = dict(event.get("metrics") or {})

    evaluation_input = PerformanceOutcomeEvaluationInput(
        strategy_id=strategy_id,
        period=period,  # type: ignore[arg-type]
        outcome_type=outcome_type,  # type: ignore[arg-type]
        title=title,
        rationale=rationale,
        metrics=metrics,
        expected_effect=event.get("expected_effect"),
        expected_risk=event.get("expected_risk"),
        source_id=source_id,
        source_type=source_type,
        source_version=source_version,
        evidence_refs=evidence_refs,
        as_of=event.get("as_of"),
        correlation_id=correlation_id or None,
    )

    suggestion = prod.produce_suggestion_from_outcome(
        tenant_id=tenant_id,
        owner_user_id=owner_user_id,
        evaluation=evaluation_input,
        utc_now=utc_now,
    )

    # Publish read-model event or workshop SSE if requested
    workshop_id = event.get("workshop_id")
    if workshop_id:
        try:
            from ..strategy_workshop.events import _ws_publish

            _ws_publish(
                workshop_id,
                "agora.performance.suggestion.created",
                {
                    "suggestion_id": suggestion.suggestion_id,
                    "strategy_id": strategy_id,
                    "outcome_type": outcome_type,
                    "correlation_id": correlation_id,
                },
            )
        except Exception as exc:
            logger.debug("Failed publishing workshop SSE for suggestion: %s", exc)

    topic = "agora.performance.suggestion.created"
    entity_id = suggestion.suggestion_id

    is_published = False
    if store is not None and hasattr(store, "is_event_published"):
        is_published = store.is_event_published(topic, entity_id)

    publisher = publish_event_fn or canonical_performance_publisher
    if not is_published and publisher:
        payload = {
            "strategy_id": strategy_id,
            "suggestion_id": suggestion.suggestion_id,
            "correlation_id": correlation_id,
            "tenant_id": tenant_id,
        }
        # Fail closed on publisher failure: do NOT swallow exceptions so retry/outage recovery works
        publisher(topic, entity_id, payload)
        if store is not None and hasattr(store, "mark_event_published"):
            store.mark_event_published(topic, entity_id, payload, published_at=utc_now)

    return suggestion


class EvaluationTelemetryConsumer:
    """Canonical evaluation and telemetry outcome consumer for Agora performance suggestions.

    Implements SD §6.4:
      Attach PerformanceSuggestionProducer to the canonical evaluation/telemetry
      consumer that owns the input event. Do not add a new scheduler. The consumer
      persists the suggestion in the selected performance store and emits its
      read-model event. The BFF only queries it.
    """

    def __init__(
        self,
        *,
        store: Optional[PerformanceSuggestionStore] = None,
        producer: Optional[PerformanceSuggestionProducer] = None,
        publish_event_fn: Optional[Callable[[str, str, Dict[str, Any]], None]] = None,
    ) -> None:
        self.store = store or PerformanceSuggestionStore()
        self.producer = producer or PerformanceSuggestionProducer(store=self.store)
        self.publish_event_fn = publish_event_fn or canonical_performance_publisher
        self._subscriptions: List[Any] = []

    def consume(
        self,
        event: Dict[str, Any],
        *,
        utc_now: Optional[str] = None,
    ) -> AdjustmentSuggestion:
        """Consume an evaluation, paper runtime, or telemetry outcome event."""
        return consume_telemetry_outcome(
            event,
            store=self.store,
            producer=self.producer,
            publish_event_fn=self.publish_event_fn,
            utc_now=utc_now,
        )

    def replay(
        self,
        event: Dict[str, Any],
        *,
        utc_now: Optional[str] = None,
    ) -> AdjustmentSuggestion:
        """Idempotently replay a previously processed telemetry outcome event."""
        return self.consume(event, utc_now=utc_now)

    def attach_to(self, telemetry_consumer: Any) -> None:
        """Attach this consumer's suggestion production to an existing canonical telemetry consumer."""
        attach_to_telemetry_consumer(
            telemetry_consumer,
            store=self.store,
            producer=self.producer,
            publish_event_fn=self.publish_event_fn,
        )
        self._subscriptions.append(telemetry_consumer)


def attach_to_telemetry_consumer(
    telemetry_consumer: Any,
    *,
    store: Optional[PerformanceSuggestionStore] = None,
    producer: Optional[PerformanceSuggestionProducer] = None,
    publish_event_fn: Optional[Callable[[str, str, Dict[str, Any]], None]] = None,
) -> None:
    """Attach PerformanceSuggestionProducer / consume_telemetry_outcome to a canonical telemetry consumer.

    Enables event-driven production of Agora AdjustmentSuggestions whenever the canonical
    consumer processes a threshold breach or evaluation outcome.
    """
    callback = lambda ev: consume_telemetry_outcome(
        ev,
        store=store,
        producer=producer,
        publish_event_fn=publish_event_fn,
    )
    if hasattr(telemetry_consumer, "attach_suggestion_consumer"):
        telemetry_consumer.attach_suggestion_consumer(callback)
    elif hasattr(telemetry_consumer, "add_subscriber"):
        telemetry_consumer.add_subscriber(callback)
    else:
        setattr(telemetry_consumer, "_suggestion_consumer", callback)
