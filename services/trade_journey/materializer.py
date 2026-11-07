"""Deterministic, source-agnostic Trade Journey read-model materializer."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import os
from typing import Any, Iterable, Mapping


STAGES = (
    "research_rationale", "strategy_candidate", "candidate_evaluation",
    "promotion_decision", "capital_binding", "deployment_runtime",
    "signal_generation", "trade_decision", "risk_evaluation",
    "order_submission", "broker_acknowledgement", "fill_management",
    "ledger_booking", "reconciliation",
)
# ``no_order``: the trade decision skipped order submission, so nothing exists to
# reconcile. ``unreconciled``: an order path stopped before reconciliation and the
# documented deadline passed; it is terminal but never a ``completed`` variant.
TERMINAL_STATUSES = {
    "completed", "completed_with_variance", "failed", "cancelled", "expired",
    "no_order", "unreconciled",
}
# Reconciliation deadline: producers emit reconciliation within hours of ledger
# booking; journeys older than this without it can no longer be reconciled.
RECONCILIATION_DEADLINE_SECONDS = int(
    os.getenv("PANTHEON_TRADE_JOURNEY_RECONCILIATION_DEADLINE_SECONDS", str(72 * 3600))
)
IDENTIFIER_FIELDS = (
    "research_journey_id", "strategy_lifecycle_id", "persona_id", "strategy_id",
    "signal_id", "decision_id",
    "risk_decision_id", "client_order_id", "order_id", "broker_order_id",
    "fill_id", "broker_trade_id", "ledger_entry_id", "reconciliation_id",
)
# Dimensions shared by many journeys; they are read from journeys.current_identity_summary,
# never bound one-journey-per-value in identity_links.
SHARED_IDENTIFIER_TYPES = frozenset({
    "strategy_id", "runtime_id", "binding_id", "capital_pool_id", "persona_id",
    "persona_capital_binding_id", "artifact_id", "artifact_version", "plan_id",
})


def identity_summary(identity: Mapping[str, Any], identifiers: Mapping[str, Any] | None) -> dict[str, Any]:
    """Nested journeys.current_identity_summary keeping every shared dimension."""
    return {"identifiers": {
        **{name: [identity[name]] for name in SHARED_IDENTIFIER_TYPES if identity.get(name)},
        **(identifiers or {}),
    }}


CLOCK_DRIFT_THRESHOLD_SECONDS = float(os.getenv("PANTHEON_TRADE_JOURNEY_CLOCK_DRIFT_SECONDS", "5"))


class MaterializationError(ValueError):
    """The event cannot safely be included in the read model."""


@dataclass(frozen=True)
class LookupKey:
    tenant_id: str
    environment: str
    identifier_type: str
    identifier: str


@dataclass
class JourneyProjection:
    journey_id: str
    tenant_id: str
    environment: str
    timeline: list[dict[str, Any]] = field(default_factory=list)
    snapshot: dict[str, Any] = field(default_factory=dict)
    graph_edges: list[dict[str, str]] = field(default_factory=list)
    diagnostics: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "journey_id": self.journey_id,
            "tenant_id": self.tenant_id,
            "environment": self.environment,
            "timeline": self.timeline,
            "snapshot": self.snapshot,
            "graph_edges": self.graph_edges,
            "diagnostics": self.diagnostics,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> JourneyProjection:
        return cls(
            journey_id=data["journey_id"],
            tenant_id=data["tenant_id"],
            environment=data["environment"],
            timeline=list(data.get("timeline") or []),
            snapshot=dict(data.get("snapshot") or {}),
            graph_edges=list(data.get("graph_edges") or []),
            diagnostics=list(data.get("diagnostics") or []),
        )



class JourneyMaterializer:
    """Build projections solely from immutable input event history.

    Ordering uses producer time, sequence, and event id, so rebuilds are stable
    even when delivery order differs. Duplicate event ids are idempotent; a
    duplicate carrying different content fails closed.
    """

    def __init__(
        self,
        *,
        as_of: datetime | None = None,
        reconciliation_deadline_seconds: int = RECONCILIATION_DEADLINE_SECONDS,
    ) -> None:
        # ``as_of`` is None for pure replays so rebuilds stay deterministic;
        # only the live projector supplies its clock for the deadline rule.
        self._as_of = as_of
        self._reconciliation_deadline_seconds = reconciliation_deadline_seconds
        self._events: dict[str, dict[str, Any]] = {}
        self._event_fingerprints: dict[str, str] = {}
        self._projections: dict[tuple[str, str, str], JourneyProjection] = {}
        self._reverse: dict[LookupKey, set[str]] = {}
        self.source_watermarks: dict[str, str] = {}
        self.revision = 0
        self.rebuild_status = "idle"

    def ingest(self, event: Mapping[str, Any]) -> bool:
        normalized = self._normalize(event)
        event_id = normalized["event_id"]
        fingerprint = repr(self._canonical(normalized))
        if event_id in self._events:
            if self._event_fingerprints[event_id] != fingerprint:
                raise MaterializationError(f"conflicting duplicate event_id: {event_id}")
            return False
        self._events[event_id] = normalized
        self._event_fingerprints[event_id] = fingerprint
        self._rematerialize()
        return True

    def rebuild(self, events: Iterable[Mapping[str, Any]]) -> None:
        self.rebuild_status = "running"
        self._events.clear()
        self._event_fingerprints.clear()
        try:
            for event in events:
                normalized = self._normalize(event)
                event_id = normalized["event_id"]
                fingerprint = repr(self._canonical(normalized))
                previous = self._event_fingerprints.get(event_id)
                if previous is not None and previous != fingerprint:
                    raise MaterializationError(f"conflicting duplicate event_id: {event_id}")
                self._events[event_id] = normalized
                self._event_fingerprints[event_id] = fingerprint
            self._rematerialize()
            self.rebuild_status = "complete"
        except Exception:
            self.rebuild_status = "failed"
            raise

    def get(self, journey_id: str, *, tenant_id: str, environment: str) -> JourneyProjection | None:
        return self._projections.get((tenant_id, environment, journey_id))

    def resolve(self, identifier_type: str, identifier: str, *, tenant_id: str,
                environment: str, allowed_journey_ids: set[str] | None = None) -> list[str]:
        matches = self._reverse.get(LookupKey(tenant_id, environment, identifier_type, identifier), set())
        if allowed_journey_ids is not None:
            matches = matches & allowed_journey_ids
        return sorted(matches)

    @property
    def projections(self) -> list[JourneyProjection]:
        return [self._projections[key] for key in sorted(self._projections)]

    def _rematerialize(self) -> None:
        self._projections = {}
        self._reverse = {}
        self.source_watermarks = {}
        grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
        for event in self._events.values():
            key = (event["tenant_id"], event["environment"], event["journey_id"])
            grouped.setdefault(key, []).append(event)
            source = event.get("source", "unknown")
            self.source_watermarks[source] = max(self.source_watermarks.get(source, ""), event["occurred_at"])
        for key, events in grouped.items():
            ordered = sorted(events, key=self._sort_key)
            projection = self._build_projection(key, ordered)
            self._projections[key] = projection
            self._index(projection)
        self.revision += 1

    def _build_projection(self, key: tuple[str, str, str], events: list[dict[str, Any]]) -> JourneyProjection:
        tenant_id, environment, journey_id = key
        stages: dict[str, dict[str, Any]] = {}
        identifiers: dict[str, set[str]] = {}
        edges: set[tuple[str, str, str]] = set()
        corrections: list[dict[str, Any]] = []
        explicit_cancel = False
        for event in events:
            stage = event.get("stage")
            if stage:
                stages[stage] = {
                    "status": event.get("stage_status", "unknown"),
                    "updated_at": event["occurred_at"],
                    "revision": sum(1 for item in events if item.get("stage") == stage and self._sort_key(item) <= self._sort_key(event)),
                }
            for identifier_type in IDENTIFIER_FIELDS:
                value = event.get(identifier_type)
                if isinstance(value, str) and value:
                    identifiers.setdefault(identifier_type, set()).add(value)
            for edge in event.get("graph_edges", []):
                if isinstance(edge, Mapping) and all(isinstance(edge.get(k), str) for k in ("from", "to", "type")):
                    edges.add((edge["from"], edge["to"], edge["type"]))
            if event.get("event_type") == "correction":
                corrections.append(event)
            explicit_cancel |= event.get("event_type") in {"cancel", "order_cancelled"}

        inferred_risk = self._inferred_nonfill_risk_stage(stages, events, identifiers)
        if inferred_risk is not None:
            stages["risk_evaluation"] = inferred_risk

        diagnostics: list[dict[str, Any]] = []
        drifted = [event for event in events if abs(float(event.get("clock_skew_seconds", 0))) > CLOCK_DRIFT_THRESHOLD_SECONDS]
        if drifted:
            diagnostics.append({
                "code": "clock_drift",
                "threshold_seconds": CLOCK_DRIFT_THRESHOLD_SECONDS,
                "max_abs_seconds": max(abs(float(event["clock_skew_seconds"])) for event in drifted),
                "event_ids": [event["event_id"] for event in drifted],
            })
        missing = self._missing_stages(stages)
        if missing:
            diagnostics.append({"code": "missing_stages", "stages": missing})
        for identifier_type, values in identifiers.items():
            if len(values) > 1 and identifier_type not in {"order_id", "broker_order_id", "broker_trade_id", "ledger_entry_id"}:
                diagnostics.append({"code": "identifier_conflict", "identifier_type": identifier_type, "values": sorted(values)})
        terminal_indicators = self._terminal_indicators(stages, explicit_cancel)
        if len(terminal_indicators) > 1:
            diagnostics.append({"code": "conflicting_terminal_states", "states": sorted(terminal_indicators)})
        if "order_id" in identifiers and not ({"client_order_id", "signal_id"} & identifiers.keys()):
            diagnostics.append({"code": "orphan_identifier", "identifier_type": "order_id"})

        status = self._rollup(stages, corrections, explicit_cancel, diagnostics)
        occurred = [event["occurred_at"] for event in events]
        if status == "open" and self._as_of is not None:
            status = self.deadline_status(
                stages, self._instant(max(occurred, key=self._instant)),
                self._as_of, self._reconciliation_deadline_seconds,
            ) or status
        snapshot = {
            "journey_id": journey_id, "tenant_id": tenant_id, "environment": environment,
            "status": status, "stages": stages, "revision": len(events),
            # Causal order may disagree with raw producer clocks under drift;
            # the temporal envelope must still satisfy first <= last.
            "created_at": min(occurred, key=self._instant), "updated_at": max(occurred, key=self._instant),
            "identifiers": {name: sorted(values) for name, values in sorted(identifiers.items())},
            "completeness": {"missing_stages": missing, "complete": not missing},
        }
        return JourneyProjection(journey_id, tenant_id, environment, events, snapshot,
                                 [{"from": a, "to": b, "type": c} for a, b, c in sorted(edges)], diagnostics)

    def _index(self, projection: JourneyProjection) -> None:
        identifiers = {"journey_id": [projection.journey_id], **projection.snapshot["identifiers"]}
        for identifier_type, values in identifiers.items():
            for value in values:
                key = LookupKey(projection.tenant_id, projection.environment, identifier_type, value)
                self._reverse.setdefault(key, set()).add(projection.journey_id)

    @staticmethod
    def _rollup(stages: Mapping[str, Mapping[str, Any]], corrections: list[dict[str, Any]],
                cancelled: bool, diagnostics: list[dict[str, Any]]) -> str:
        if any(item["code"] == "conflicting_terminal_states" for item in diagnostics):
            return "incomplete"
        statuses = {name: info["status"] for name, info in stages.items()}
        if cancelled:
            return "cancelled"
        if any(value == "waiting_human" for value in statuses.values()):
            return "waiting_human"
        if any(value == "blocked" for value in statuses.values()):
            return "blocked"
        if any(statuses.get(name) in {"rejected", "failed"} for name in ("risk_evaluation", "order_submission", "broker_acknowledgement")):
            return "failed"
        if statuses.get("reconciliation") == "failed":
            return "completed" if corrections else "completed_with_variance"
        if statuses.get("reconciliation") == "succeeded":
            return "completed"
        if statuses.get("fill_management") == "partially_succeeded":
            return "partially_filled"
        if statuses.get("order_submission") == "succeeded" and statuses.get("fill_management") == "active":
            return "executing"
        if statuses.get("order_submission") == "skipped" or (
            statuses.get("trade_decision") == "skipped" and "order_submission" not in statuses
        ):
            return "no_order"
        return "open" if stages else "incomplete"

    @staticmethod
    def _inferred_nonfill_risk_stage(
        stages: Mapping[str, Mapping[str, Any]],
        events: list[dict[str, Any]],
        identifiers: Mapping[str, set[str]],
    ) -> dict[str, Any] | None:
        if "risk_evaluation" in stages:
            return None
        if stages.get("signal_generation", {}).get("status") != "succeeded":
            return None
        if stages.get("trade_decision", {}).get("status") not in {"succeeded", "skipped"}:
            return None
        if {"broker_acknowledgement", "fill_management", "ledger_booking", "reconciliation"} & set(stages):
            return None
        if {"fill_id", "broker_trade_id", "ledger_entry_id"} & set(identifiers):
            return None
        order_stage = stages.get("order_submission")
        if not isinstance(order_stage, Mapping):
            return None
        order_status = order_stage.get("status")
        submission_events = [item for item in events if item.get("stage") == "order_submission"]
        if not submission_events or any(
            item.get("submitted_to_broker") is True
            or item.get("broker_order_id")
            or item.get("fill_id")
            or item.get("broker_trade_id")
            or item.get("ledger_entry_id")
            for item in submission_events
        ):
            return None
        if order_status == "skipped" and (
            stages.get("trade_decision", {}).get("status") == "skipped"
            or any(
                item.get("event_type") == "paper_order_simulated"
                and item.get("submitted_to_broker") is not True
                and item.get("quantity") in (None, 0, 0.0)
                and (
                    str(item.get("decision_status") or "").strip().lower() == "no_order"
                    or str(item.get("order_status") or "").strip().lower() == "not_submitted"
                    or str(item.get("risk_status") or "").strip().lower() == "skipped"
                    or bool(item.get("noop_reason") or item.get("reason_code"))
                )
                for item in submission_events
            )
        ):
            return {
                "status": "skipped",
                "updated_at": order_stage["updated_at"],
                "revision": order_stage.get("revision", 1),
            }
        if order_status in {"failed", "rejected"} and any(
            item.get("event_type") in {"order_rejection", "order_rejection_simulated"}
            and item.get("submitted_to_broker") is not True
            and item.get("quantity") in (None, 0, 0.0)
            and str(
                item.get("reason_code")
                or item.get("reject_reason")
                or item.get("reason")
                or item.get("risk_reason")
                or item.get("broker_submission_status")
                or ""
            ).strip()
            == "sell_without_holdings"
            for item in submission_events
        ):
            return {
                "status": "rejected",
                "updated_at": order_stage["updated_at"],
                "revision": order_stage.get("revision", 1),
            }
        return None

    @staticmethod
    def deadline_status(stages: Mapping[str, Any], last_occurred: datetime, as_of: datetime,
                        deadline_seconds: int = RECONCILIATION_DEADLINE_SECONDS) -> str | None:
        """Terminal status for an open journey past the reconciliation deadline.

        Returns None while the journey can still be reconciled. A journey that
        never reached order submission has nothing to reconcile (``no_order``);
        one with an order but no reconciliation is ``unreconciled``.
        """
        if (as_of - last_occurred).total_seconds() <= deadline_seconds:
            return None
        if "reconciliation" in stages:
            return None
        return "unreconciled" if "order_submission" in stages else "no_order"

    @staticmethod
    def _terminal_indicators(stages: Mapping[str, Mapping[str, Any]], cancelled: bool) -> set[str]:
        result = {"cancelled"} if cancelled else set()
        if any(stages.get(name, {}).get("status") in {"rejected", "failed"} for name in ("risk_evaluation", "order_submission", "broker_acknowledgement")):
            result.add("failed")
        if stages.get("reconciliation", {}).get("status") == "succeeded":
            result.add("completed")
        return result

    @staticmethod
    def _missing_stages(stages: Mapping[str, Any]) -> list[str]:
        if not stages:
            return list(STAGES)
        furthest = max(STAGES.index(stage) for stage in stages)
        return [stage for stage in STAGES[:furthest + 1] if stage not in stages]

    @staticmethod
    def _instant(value: Any) -> datetime:
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

    @staticmethod
    def _sort_key(event: Mapping[str, Any]) -> tuple[int, int, str, str, str]:
        # Canonical aggregate sequence takes precedence over producer clocks.
        # Legacy events without a sequence retain deterministic event-time
        # ordering so old stores remain readable while lifecycle-projector
        # output follows the L1 ``sequence_no`` contract.
        raw_sequence = event.get("sequence_no", event.get("sequence"))
        if raw_sequence is None:
            return (
                1,
                0,
                event.get("ordering_at", event["occurred_at"]),
                event["occurred_at"],
                event["event_id"],
            )
        try:
            sequence = int(raw_sequence)
        except (TypeError, ValueError) as exc:
            raise MaterializationError("sequence_no must be an integer") from exc
        return (
            0,
            sequence,
            event.get("ordering_at", event["occurred_at"]),
            event["occurred_at"],
            event["event_id"],
        )

    @classmethod
    def _normalize(cls, event: Mapping[str, Any]) -> dict[str, Any]:
        result = dict(event)
        envelope = result.pop("correlation_envelope", None)
        if isinstance(envelope, Mapping):
            for name in ("journey_id", "tenant_id", "environment", "correlation_id", "trace_id", "research_journey_id", "strategy_lifecycle_id"):
                result.setdefault(name, envelope.get(name))
        required = ("event_id", "journey_id", "tenant_id", "environment", "occurred_at")
        missing = [name for name in required if not isinstance(result.get(name), str) or not result[name]]
        if missing:
            raise MaterializationError(f"missing required fields: {', '.join(missing)}")
        if result.get("stage") not in (None, *STAGES):
            raise MaterializationError(f"unknown stage: {result['stage']}")
        try:
            occurred = datetime.fromisoformat(result["occurred_at"].replace("Z", "+00:00"))
            if occurred.tzinfo is None:
                raise ValueError
            occurred = occurred.astimezone(timezone.utc)
            result["occurred_at"] = occurred.isoformat(
                timespec="microseconds"
            ).replace("+00:00", "Z")
            raw_recorded = result.get("recorded_at")
            if raw_recorded:
                recorded = datetime.fromisoformat(str(raw_recorded).replace("Z", "+00:00"))
                if recorded.tzinfo is None:
                    raise ValueError
                recorded = recorded.astimezone(timezone.utc)
                result["recorded_at"] = recorded.isoformat(timespec="microseconds").replace("+00:00", "Z")
                skew = (recorded - occurred).total_seconds()
                result["clock_skew_seconds"] = skew
                result["ordering_at"] = result["recorded_at"] if abs(skew) > CLOCK_DRIFT_THRESHOLD_SECONDS else result["occurred_at"]
            else:
                result["ordering_at"] = result["occurred_at"]
        except ValueError as exc:
            raise MaterializationError("occurred_at and recorded_at must be timezone-aware ISO-8601") from exc
        return result

    @classmethod
    def _canonical(cls, value: Any) -> Any:
        if isinstance(value, Mapping):
            return tuple((key, cls._canonical(item)) for key, item in sorted(value.items()))
        if isinstance(value, list):
            return tuple(cls._canonical(item) for item in value)
        return value
