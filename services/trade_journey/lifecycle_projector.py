"""Durable canonical telemetry -> Trade Journey and loop-run PostgreSQL projector.

The projector consumes committed ``telemetry_events`` rows by the monotonic
``ingested_seq`` assigned by Postgres. It updates durable projection tables and
receipts via ``ProjectionStore`` transactions. Legacy JSON files and file-based
writer/read models are retired.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import sys
from typing import Any, Callable, Iterable, Mapping, Sequence

from services.trade_journey.correlation_envelope import (
    CorrelationEnvelopeError,
    validate_envelope,
)
from services.trade_journey.incremental_materializer import IncrementalLifecycleMaterializer
from services.trade_journey.materializer import (
    IDENTIFIER_FIELDS,
    TERMINAL_STATUSES,
    JourneyMaterializer,
)
from services.trade_journey.projection_store import (
    BatchProjectionMutation,
    EventReceiptRow,
    IdentityLinkRow,
    JourneyRow,
    JourneyStageRow,
    LoopRunRow,
    ProjectionStore,
    QuarantineRow,
)


JOURNEY_STORE_SCHEMA = "pantheon.trade-journey-projection.v1"
LOOP_STORE_SCHEMA = "pantheon.loop-run-projection.v1"
PROJECTION_MODES = frozenset({"live", "recovery", "backfill", "replay"})

DEFAULT_CHANNEL = "pantheon_lifecycle_events"
DEFAULT_SOURCE_STARTUP_TIMEOUT_SECONDS = 10.0
DEFAULT_SOURCE_TIMEOUT_SECONDS = 10.0
RELATIONAL_WRITER_BACKEND_ENV = "LIFECYCLE_PROJECTOR_WRITER_BACKEND"
RELATIONAL_WRITER_DSN_ENV = "LIFECYCLE_PROJECTOR_PROJECTION_DSN"
RELATIONAL_WRITER_SCHEMA_ENV = "LIFECYCLE_PROJECTOR_PROJECTION_SCHEMA"
RELATIONAL_WRITER_BACKEND_SHADOW = "shadow"
RELATIONAL_WRITER_DEFAULT_SCHEMA = "trade_journey_projection"
RELATIONAL_CONTROLLER_ID = "canonical-lifecycle-projector"
RELATIONAL_CONTROLLER_TENANT_SCOPE = "*"
RELATIONAL_CONTROLLER_ENVIRONMENT_SCOPE = "*"

LIFECYCLE_EVENT_TYPES = frozenset(
    {
        "trade_journey_fixture",
        "signal_generation",
        "trade_decision",
        "risk_evaluation",
        "paper_order_simulated",
        "order_submitted",
        "order_accepted",
        "order_partially_filled",
        "paper_fill_simulated",
        "fill_received",
        "order_filled",
        "order_rejection",
        "order_rejection_simulated",
        "order_canceled",
        "order_cancelled",
        "position_snapshot",
        "position_snapshot_received",
        "broker_position_snapshot",
        "reconciliation_completed",
        "reconciliation_failed",
    }
)
LIFECYCLE_EVENT_TYPE_QUERY = tuple(sorted(LIFECYCLE_EVENT_TYPES))

FIXTURE_EVENT_TYPE = "trade_journey_fixture"
FIXTURE_SCHEMA_VERSION = "pantheon.trade-journey-fixture.v1"
FIXTURE_SOURCE = "tj_e2e_012_hosted_seed_v3"
FIXTURE_TENANT_ID = "tenant-dev"
FIXTURE_STAGES = frozenset(
    {
        "signal_generation",
        "trade_decision",
        "promotion_decision",
        "risk_evaluation",
        "order_submission",
        "broker_acknowledgement",
        "fill_management",
        "ledger_booking",
        "reconciliation",
    }
)
FIXTURE_PASSTHROUGH_FIELDS = frozenset(
    {
        "account_id",
        "artifact_version",
        "binding_version",
        "broker_order_id",
        "broker_trade_id",
        "capital_account_id",
        "causation_id",
        "client_order_id",
        "decision_id",
        "due_at",
        "event_type",
        "evidence_refs",
        "failing_check",
        "fill_id",
        "filled_quantity",
        "graph_edges",
        "human_inbox_ref",
        "incident_id",
        "input_refs",
        "next_action",
        "order_id",
        "order_state",
        "owner_role",
        "parent_order_id",
        "persona_id",
        "persona_version",
        "policy_refs",
        "policy_version",
        "price",
        "quantity",
        "reason_code",
        "recorded_at",
        "replaced_order_id",
        "remaining_quantity",
        "remediation_ref",
        "research_journey_id",
        "return_url",
        "signal_id",
        "source_ref",
        "source_status",
        "source_unavailable",
        "strategy_id",
        "strategy_lifecycle_id",
        "strategy_version",
        "summary",
        "unavailable_sources",
        "unfilled_quantity",
        "variance",
    }
)

STABLE_IDENTITY_FIELDS = (
    "tenant_id",
    "environment",
    "journey_id",
    "run_id",
    "loop_run_id",
    "signal_id",
    "strategy_id",
    "runtime_id",
    "binding_id",
    "capital_pool_id",
    "persona_id",
    "persona_capital_binding_id",
    "artifact_id",
    "artifact_version",
    "plan_id",
    "trace_id",
)

_PASSTHROUGH_FIELDS = (
    "decision_id",
    "risk_decision_id",
    "client_order_id",
    "order_id",
    "broker_order_id",
    "broker_trade_id",
    "ledger_entry_id",
    "reconciliation_id",
    "symbol",
    "side",
    "quantity",
    "price",
)


class LifecycleProjectionError(RuntimeError):
    """Base error for projection failures that must fail closed."""


class ConflictingLifecycleEvent(LifecycleProjectionError):
    """An existing canonical event id was reused with different content."""


class InvalidLifecycleEvent(LifecycleProjectionError):
    """A lifecycle event lacks canonical identity or ordering evidence."""


@dataclass(frozen=True)
class ProjectionResult:
    checkpoint: int
    accepted: int
    duplicates: int
    ignored: int
    quarantined: int
    journey_count: int
    loop_run_count: int
    generation: int
    mode: str


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _parse_iso(value: Any) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise InvalidLifecycleEvent(f"invalid timezone-aware timestamp: {value!r}")
        return value.astimezone(timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise InvalidLifecycleEvent(f"invalid timezone-aware timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        raise InvalidLifecycleEvent(f"invalid timezone-aware timestamp: {value!r}")
    return parsed.astimezone(timezone.utc)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _clean(value: Any) -> Any:
    return None if value in (None, "", [], {}) else value


def _first(*values: Any) -> Any:
    for value in values:
        cleaned = _clean(value)
        if cleaned is not None:
            return cleaned
    return None


def _stage_status(value: Any) -> str:
    token = str(value or "unknown").strip().lower()
    if token in {"ok", "accepted", "submitted", "filled", "resolved", "complete", "completed", "succeeded"}:
        return "succeeded"
    if token in {"partial", "partially_filled", "partially_succeeded"}:
        return "partially_succeeded"
    if token in {"rejected", "failed", "error"}:
        return "failed"
    if token in {"cancelled", "canceled"}:
        return "cancelled"
    if token in {"noop", "no_order", "not_submitted", "skipped"}:
        return "skipped"
    return token


class RelationalLifecycleProjector:
    """Canonical bounded relational lifecycle projector.

    Durable cursor, receipts, quarantines, aggregate summaries, and controller
    revision all live in the PostgreSQL projection schema and are committed by
    atomic ``ProjectionStore`` transactions. Legacy JSON file-based generation and
    read-model paths are retired.
    """

    def __init__(
        self,
        store: ProjectionStore,
        *,
        deployment_sha: str = "unknown",
        clock: Callable[[], str] = _utc_now,
        controller_id: str = RELATIONAL_CONTROLLER_ID,
        tenant_scope: str = RELATIONAL_CONTROLLER_TENANT_SCOPE,
        environment_scope: str = RELATIONAL_CONTROLLER_ENVIRONMENT_SCOPE,
    ) -> None:
        self.store = store
        self.deployment_sha = str(deployment_sha or "unknown")
        self.clock = clock
        self.controller_id = str(controller_id)
        self.tenant_scope = str(tenant_scope)
        self.environment_scope = str(environment_scope)
        self._controller_state = self.store.get_controller_state(
            self.controller_id, self.tenant_scope, self.environment_scope
        )
        self._materializer = IncrementalLifecycleMaterializer(
            journey_events_fn=self._journey_events
        )

    @property
    def checkpoint(self) -> int:
        return int(self._controller().checkpoint_seq)

    @property
    def controller(self) -> dict[str, Any]:
        state = self._controller()
        return {
            "controller_id": state.controller_id,
            "checkpoint": state.checkpoint_seq,
            "source_high_watermark": state.source_high_watermark,
            "backlog": state.backlog_count,
            "generation": state.projection_revision,
            "deployment_sha": state.deployment_sha,
            "mode": state.mode,
            "status": state.status,
            "accepted_live": state.accepted_live,
            "quarantine_count": state.unresolved_quarantine_count,
            "last_error": state.last_error_message or None,
        }

    def _controller(self):
        if self._controller_state is None:
            from services.trade_journey.projection_store import ControllerStateRow

            return ControllerStateRow(
                controller_id=self.controller_id,
                tenant_scope=self.tenant_scope,
                environment_scope=self.environment_scope,
                checkpoint_seq=0,
                source_high_watermark=0,
                backlog_count=0,
                projection_revision=0,
                deployment_sha=self.deployment_sha,
                mode="recovery",
                status="initializing",
                accepted_live=False,
            )
        return self._controller_state

    @staticmethod
    def _row_event_id(row: Mapping[str, Any]) -> str:
        payload = row.get("payload")
        candidate = row.get("event_id")
        if candidate in (None, "") and isinstance(payload, Mapping):
            candidate = payload.get("event_id")
        value = str(candidate or "").strip()
        if not value:
            raise InvalidLifecycleEvent("source row missing event_id")
        return value

    @staticmethod
    def _row_event_type(row: Mapping[str, Any]) -> str:
        payload = row.get("payload")
        candidate = row.get("event_type")
        if candidate in (None, "") and isinstance(payload, Mapping):
            candidate = payload.get("event_type")
        return str(candidate or "unknown")

    def _row_fingerprint(self, row: Mapping[str, Any], event: Mapping[str, Any] | None) -> str:
        if event is not None:
            return _fingerprint(
                {
                    "event_id": event.get("event_id"),
                    "event_type": event.get("event_type"),
                    "created_at": event.get("created_at"),
                    "payload": event,
                }
            )
        return _fingerprint(
            {
                "event_id": self._row_event_id(row),
                "event_type": self._row_event_type(row),
                "created_at": row.get("created_at"),
                "payload": row.get("payload"),
            }
        )

    def _receipt(
        self,
        *,
        event_id: str,
        ingested_seq: int,
        fingerprint: str,
        source_event_type: str,
        created_at: datetime,
        disposition: str,
        identity: Mapping[str, str] | None = None,
    ) -> EventReceiptRow:
        identity = identity or {}
        return EventReceiptRow(
            event_id=event_id,
            ingested_seq=ingested_seq,
            fingerprint=fingerprint,
            tenant_id=str(identity.get("tenant_id") or ""),
            environment=str(identity.get("environment") or ""),
            journey_id=str(identity.get("journey_id") or ""),
            loop_run_id=str(identity.get("loop_run_id") or ""),
            source_event_type=source_event_type,
            created_at=created_at,
            disposition=disposition,
            projection_revision=0,
        )

    def _hydrate_materializer(
        self, entries: Sequence[Mapping[str, Any]]
    ) -> IncrementalLifecycleMaterializer:
        materializer = IncrementalLifecycleMaterializer(
            journey_events_fn=self._journey_events
        )
        affected = {
            (
                str(entry["identity"]["tenant_id"]),
                str(entry["identity"]["environment"]),
                str(entry["identity"]["journey_id"]),
            )
            for entry in entries
        }
        affected_keys = tuple(sorted(affected))
        prior_events = self.store.load_journey_stage_events_bulk(affected_keys)
        for tenant_id, environment, journey_id in affected_keys:
            materializer.hydrate_aggregate(
                journey_id,
                prior_events[(tenant_id, environment, journey_id)],
            )
        materializer.reset_stats()
        return materializer

    @staticmethod
    def _identity_links_for_aggregate(agg: Any) -> list[IdentityLinkRow]:
        observations: dict[tuple[str, str], list[tuple[int, datetime]]] = {}
        for event in agg.journey_events:
            occurred_at = _parse_iso(event.get("occurred_at"))
            source_offset = int(event.get("source_offset") or 0)
            if source_offset <= 0:
                continue
            values: dict[str, Any] = {
                "journey_id": event.get("journey_id"),
                "run_id": event.get("run_id"),
                "loop_run_id": event.get("loop_run_id"),
                "signal_id": event.get("signal_id"),
                "strategy_id": event.get("strategy_id"),
                "runtime_id": event.get("runtime_id"),
                "binding_id": event.get("binding_id"),
                "capital_pool_id": event.get("capital_pool_id"),
                "persona_id": event.get("persona_id"),
                "persona_capital_binding_id": event.get("persona_capital_binding_id"),
                "artifact_id": event.get("artifact_id"),
                "artifact_version": event.get("artifact_version"),
                "plan_id": event.get("plan_id"),
                "trace_id": event.get("trace_id"),
            }
            values.update(
                {
                    identifier: event.get(identifier)
                    for identifier in IDENTIFIER_FIELDS
                }
            )
            for identifier_type, value in values.items():
                if isinstance(value, str) and value:
                    observations.setdefault((identifier_type, value), []).append(
                        (source_offset, occurred_at)
                    )
        links: list[IdentityLinkRow] = []
        for (identifier_type, identifier_value), values in sorted(observations.items()):
            links.append(
                IdentityLinkRow(
                    tenant_id=agg.tenant_id,
                    environment=agg.environment,
                    identifier_type=identifier_type,
                    identifier_value=identifier_value,
                    journey_id=agg.journey_id,
                    first_ingested_seq=min(value[0] for value in values),
                    last_ingested_seq=max(value[0] for value in values),
                    first_occurred_at=min(value[1] for value in values),
                    last_occurred_at=max(value[1] for value in values),
                )
            )
        return links

    def _aggregate_mutations(
        self,
        staged: Mapping[str, Any],
        entries: Sequence[Mapping[str, Any]],
        *,
        mode: str,
        accepted_live: bool,
    ) -> tuple[list[IdentityLinkRow], list[JourneyRow], list[JourneyStageRow], list[LoopRunRow]]:
        entries_by_event_id = {
            str(entry["event"]["event_id"]): entry for entry in entries
        }
        identity_links: list[IdentityLinkRow] = []
        journeys: list[JourneyRow] = []
        stages: list[JourneyStageRow] = []
        loop_runs: list[LoopRunRow] = []
        for journey_id, agg in sorted(staged.items()):
            source_event_ids = {
                str(entry["event"]["event_id"])
                for entry in entries
                if str(entry["identity"]["journey_id"]) == journey_id
            }
            if not source_event_ids:
                continue
            identity_links.extend(self._identity_links_for_aggregate(agg))
            reducer = JourneyMaterializer()
            reducer.rebuild(agg.journey_events)
            projection = reducer.get(
                journey_id, tenant_id=agg.tenant_id, environment=agg.environment
            )
            if projection is None:
                continue
            snapshot = projection.snapshot
            journeys.append(
                JourneyRow(
                    tenant_id=agg.tenant_id,
                    environment=agg.environment,
                    journey_id=journey_id,
                    status=str(snapshot.get("status") or "incomplete"),
                    stage_coverage=dict(snapshot.get("stages") or {}),
                    is_terminal=str(snapshot.get("status") or "") in TERMINAL_STATUSES,
                    first_occurred_at=_parse_iso(snapshot["created_at"]),
                    last_occurred_at=_parse_iso(snapshot["updated_at"]),
                    first_ingested_seq=min(
                        int(event.get("source_offset") or 0)
                        for event in agg.journey_events
                        if int(event.get("source_offset") or 0) > 0
                    ),
                    last_ingested_seq=max(
                        int(event.get("source_offset") or 0)
                        for event in agg.journey_events
                    ),
                    current_identity_summary={"identifiers": snapshot.get("identifiers") or {}},
                    evidence_summary={
                        "event_count": len(agg.journey_events),
                        "stage_event_ids": [
                            event.get("event_id") for event in projection.timeline
                        ],
                    },
                    diagnostic_summary={"diagnostics": projection.diagnostics},
                    loop_run_id=str(agg.identity.get("loop_run_id") or ""),
                )
            )
            for stage_event in agg.journey_events:
                canonical_event_id = str(stage_event.get("canonical_event_id") or "")
                if canonical_event_id not in source_event_ids:
                    continue
                entry = entries_by_event_id[canonical_event_id]
                source_sequence = int(stage_event.get("source_sequence_no") or 0)
                stage_sequence = int(stage_event.get("sequence_no") or 0)
                evidence_refs = stage_event.get("evidence_refs")
                stages.append(
                    JourneyStageRow(
                        tenant_id=agg.tenant_id,
                        environment=agg.environment,
                        journey_id=journey_id,
                        source_event_id=canonical_event_id,
                        stage_name=str(stage_event.get("stage") or ""),
                        stage_status=str(stage_event.get("stage_status") or "unknown"),
                        stage_ordinal=max(0, stage_sequence - source_sequence * 100),
                        source_ingested_seq=int(stage_event.get("source_offset") or 0),
                        event_sequence=source_sequence,
                        occurred_at=_parse_iso(stage_event["occurred_at"]),
                        contract_fields=dict(stage_event),
                        evidence_references=(
                            list(evidence_refs)
                            if isinstance(evidence_refs, list)
                            else []
                        ),
                        fingerprint=_fingerprint(
                            {
                                "canonical_fingerprint": entry["fingerprint"],
                                "stage": stage_event.get("stage"),
                            }
                        ),
                    )
                )
            if agg.loop_record:
                loop_record = dict(agg.loop_record)
                freshness = {
                    "mode": mode,
                    "accepted_live": accepted_live,
                    "source_modes": loop_record.get("source_modes") or [],
                    "last_source_offset": loop_record.get("last_source_offset"),
                }
                loop_runs.append(
                    LoopRunRow(
                        tenant_id=agg.tenant_id,
                        environment=agg.environment,
                        loop_run_id=str(loop_record["loop_run_id"]),
                        journey_id=journey_id,
                        status=str(loop_record.get("status") or "active"),
                        lifecycle_summary=loop_record,
                        freshness_lineage=freshness,
                        contract_payload=loop_record,
                    )
                )
        return identity_links, journeys, stages, loop_runs

    def _controller_mutation(
        self,
        *,
        source_high_watermark: int,
        mode: str,
        quarantined: int,
        error_message: str = "",
        backlog: int | None = None,
    ) -> BatchProjectionMutation:
        previous = self._controller()
        optimistic_backlog = (
            max(0, int(backlog))
            if backlog is not None
            else max(
                0,
                int(source_high_watermark) - int(previous.checkpoint_seq),
            )
        )
        accepted_live = (
            mode == "live"
            and optimistic_backlog == 0
            and quarantined == 0
            and int(previous.unresolved_quarantine_count) == 0
            and not error_message
        )
        status = (
            "failed"
            if error_message
            else "degraded"
            if quarantined or int(previous.unresolved_quarantine_count) > 0
            else "ready"
            if accepted_live
            else "recovering"
            if mode == "recovery" or optimistic_backlog
            else "repair_only"
        )
        return BatchProjectionMutation(
            source_high_watermark=max(0, int(source_high_watermark)),
            backlog_count=optimistic_backlog,
            mode=mode,
            status=status,
            accepted_live=accepted_live,
            deployment_sha=self.deployment_sha,
            error_message=error_message,
        )

    @staticmethod
    def _validate_fixture_event(event: Mapping[str, Any]) -> None:
        if event.get("event_type") != FIXTURE_EVENT_TYPE:
            return
        if os.getenv("PANTHEON_TJ_E2E_FIXTURE_INGEST_ENABLED", "").lower() != "true":
            raise InvalidLifecycleEvent("dev fixture projection is disabled")
        metadata = event.get("metadata")
        envelope = event.get("correlation_envelope")
        if not isinstance(metadata, Mapping) or not isinstance(envelope, Mapping):
            raise InvalidLifecycleEvent("dev fixture metadata and correlation envelope are required")
        if (
            metadata.get("fixture_schema_version") != FIXTURE_SCHEMA_VERSION
            or metadata.get("fixture_source") != FIXTURE_SOURCE
            or metadata.get("fixture_scope") != "dev-only"
            or envelope.get("tenant_id") != FIXTURE_TENANT_ID
        ):
            raise InvalidLifecycleEvent("dev fixture scope is invalid")
        if metadata.get("fixture_stage") not in FIXTURE_STAGES:
            raise InvalidLifecycleEvent("dev fixture stage is invalid")
        if not str(metadata.get("fixture_stage_status") or "").strip():
            raise InvalidLifecycleEvent("dev fixture stage status is required")
        _parse_iso(metadata.get("fixture_occurred_at"))
        _parse_iso(metadata.get("fixture_recorded_at"))
        if not isinstance(metadata.get("fixture_payload"), Mapping):
            raise InvalidLifecycleEvent("dev fixture payload must be an object")

    @staticmethod
    def _source_event(row: Mapping[str, Any]) -> dict[str, Any]:
        payload = row.get("payload")
        event = dict(payload) if isinstance(payload, Mapping) else dict(row)
        for field in ("event_id", "event_type", "created_at"):
            row_value = _clean(row.get(field))
            event_value = _clean(event.get(field))
            if row_value is not None and event_value is not None:
                if field == "created_at":
                    if _parse_iso(row_value) != _parse_iso(event_value):
                        raise InvalidLifecycleEvent(f"source row/payload {field} mismatch")
                elif str(row_value) != str(event_value):
                    raise InvalidLifecycleEvent(f"source row/payload {field} mismatch")
            if event_value is None and row_value is not None:
                event[field] = row_value
            if _clean(event.get(field)) is None:
                raise InvalidLifecycleEvent(f"source event missing {field}")
        _parse_iso(event["created_at"])
        return event

    @staticmethod
    def _sequence_no(event: Mapping[str, Any]) -> int:
        metadata = event.get("metadata") if isinstance(event.get("metadata"), Mapping) else {}
        raw = _first(event.get("sequence_no"), metadata.get("sequence_no"))
        if isinstance(raw, bool):
            raise InvalidLifecycleEvent("sequence_no must be a positive integer")
        try:
            value = int(raw)
        except (TypeError, ValueError) as exc:
            raise InvalidLifecycleEvent("sequence_no must be a positive integer") from exc
        if value < 1:
            raise InvalidLifecycleEvent("sequence_no must be a positive integer")
        return value

    @staticmethod
    def _identity(event: Mapping[str, Any]) -> dict[str, str]:
        metadata = event.get("metadata") if isinstance(event.get("metadata"), Mapping) else {}
        authority = event.get("authority_refs") if isinstance(event.get("authority_refs"), Mapping) else {}
        target = event.get("target") if isinstance(event.get("target"), Mapping) else {}
        raw_envelope = event.get("correlation_envelope")
        if not isinstance(raw_envelope, Mapping):
            raise InvalidLifecycleEvent("correlation_envelope is required")
        try:
            envelope = validate_envelope(raw_envelope)
        except CorrelationEnvelopeError as exc:
            raise InvalidLifecycleEvent(f"invalid correlation_envelope: {exc}") from exc
        run_id = _first(event.get("run_id"), metadata.get("run_id"))
        identity: dict[str, Any] = {
            "tenant_id": _first(envelope.get("tenant_id"), event.get("tenant_id"), metadata.get("tenant_id")),
            "environment": _first(envelope.get("environment"), event.get("deployment_stage"), event.get("environment")),
            "journey_id": envelope.get("journey_id"),
            "run_id": run_id,
            "loop_run_id": _first(event.get("loop_run_id"), metadata.get("loop_run_id"), f"lr-{run_id}" if run_id else None),
            "signal_id": _first(event.get("signal_id"), metadata.get("signal_id")),
            "strategy_id": _first(target.get("strategy_id"), event.get("strategy_id"), metadata.get("strategy_id")),
            "runtime_id": event.get("runtime_id"),
            "binding_id": event.get("binding_id"),
            "capital_pool_id": event.get("capital_pool_id"),
            "persona_id": _first(authority.get("persona_id"), metadata.get("persona_id"), event.get("persona_id")),
            "persona_capital_binding_id": event.get("persona_capital_binding_id"),
            "artifact_id": event.get("artifact_id"),
            "artifact_version": event.get("artifact_version"),
            "plan_id": _first(event.get("plan_id"), event.get("deployment_plan_id")),
            "trace_id": _first(envelope.get("trace_id"), event.get("trace_id")),
        }
        missing = [field for field in STABLE_IDENTITY_FIELDS if _clean(identity.get(field)) is None]
        if missing:
            raise InvalidLifecycleEvent(
                "canonical lifecycle identity missing: " + ", ".join(missing)
            )
        if str(identity["environment"]) != str(envelope["environment"]):
            raise InvalidLifecycleEvent("environment conflicts with correlation envelope")
        return {field: str(identity[field]) for field in STABLE_IDENTITY_FIELDS}

    @classmethod
    def _journey_events(cls, entry: Mapping[str, Any]) -> list[dict[str, Any]]:
        source = entry["event"]
        identity = entry["identity"]
        specs = cls._stage_specs(source)
        metadata = source.get("metadata") if isinstance(source.get("metadata"), Mapping) else {}
        fixture_payload = (
            metadata.get("fixture_payload")
            if source.get("event_type") == FIXTURE_EVENT_TYPE
            and isinstance(metadata.get("fixture_payload"), Mapping)
            else {}
        )
        occurred_at = (
            metadata.get("fixture_occurred_at")
            if source.get("event_type") == FIXTURE_EVENT_TYPE
            else source["created_at"]
        )
        recorded_at = (
            metadata.get("fixture_recorded_at")
            if source.get("event_type") == FIXTURE_EVENT_TYPE
            else entry.get("ingested_at") or source["created_at"]
        )
        result: list[dict[str, Any]] = []
        for ordinal, (stage, status) in enumerate(specs, start=1):
            event = {
                "event_id": f"{source['event_id']}:{stage}",
                "event_type": fixture_payload.get("event_type") or source["event_type"],
                "journey_id": identity["journey_id"],
                "tenant_id": identity["tenant_id"],
                "environment": identity["environment"],
                "occurred_at": occurred_at,
                "recorded_at": recorded_at,
                "source": f"canonical_telemetry_{entry['source_mode']}",
                "source_mode": entry["source_mode"],
                "accepted_live": bool(entry.get("accepted_live")),
                "canonical_event_id": source["event_id"],
                "source_offset": entry.get("ingested_seq"),
                "source_sequence_no": int(entry["sequence_no"]),
                "sequence_no": int(entry["sequence_no"]) * 100 + ordinal,
                "sequence": int(entry["sequence_no"]) * 100 + ordinal,
                "causal_parent_id": _first(
                    source.get("causal_parent_id"),
                    metadata.get("causal_parent_id"),
                    (source.get("correlation_envelope") or {}).get("causation_event_id"),
                ),
                "stage": stage,
                "stage_status": status,
                "correlation_envelope": source.get("correlation_envelope"),
                **identity,
            }
            for field in _PASSTHROUGH_FIELDS:
                value = _first(source.get(field), metadata.get(field))
                if value is not None:
                    event[field] = value
            if source.get("event_type") == FIXTURE_EVENT_TYPE:
                for field in FIXTURE_PASSTHROUGH_FIELDS:
                    value = fixture_payload.get(field)
                    if value not in (None, "", [], {}):
                        event[field] = value
            metrics = source.get("metrics") if isinstance(source.get("metrics"), Mapping) else {}
            if "quantity" not in event:
                quantity = _first(metrics.get("fill_quantity"), source.get("position_qty"))
                if quantity is not None:
                    event["quantity"] = abs(quantity) if isinstance(quantity, (int, float)) else quantity
            if "price" not in event and _clean(metrics.get("fill_price")) is not None:
                event["price"] = metrics["fill_price"]
            result.append({key: value for key, value in event.items() if value is not None})
        return result

    @staticmethod
    def _stage_specs(event: Mapping[str, Any]) -> list[tuple[str, str]]:
        event_type = str(event["event_type"])
        metadata = event.get("metadata") if isinstance(event.get("metadata"), Mapping) else {}
        if event_type == FIXTURE_EVENT_TYPE:
            return [
                (
                    str(metadata.get("fixture_stage")),
                    str(metadata.get("fixture_stage_status")),
                )
            ]
        if event_type == "signal_generation":
            return [("signal_generation", "succeeded")]
        if event_type == "trade_decision":
            return [("trade_decision", _stage_status(metadata.get("decision_status") or "succeeded"))]
        if event_type == "risk_evaluation":
            return [("risk_evaluation", _stage_status(metadata.get("risk_status") or "succeeded"))]
        if event_type == "paper_order_simulated":
            return [
                ("order_submission", _stage_status(metadata.get("order_status") or "succeeded")),
            ]
        if event_type == "order_submitted":
            return [("order_submission", "succeeded")]
        if event_type == "order_accepted":
            return [("broker_acknowledgement", "succeeded")]
        if event_type == "order_partially_filled":
            return [("fill_management", "partially_succeeded")]
        if event_type in {"paper_fill_simulated", "fill_received", "order_filled"}:
            return [("fill_management", "succeeded")]
        if event_type in {"order_rejection", "order_rejection_simulated"}:
            return [("order_submission", "failed")]
        if event_type in {"order_canceled", "order_cancelled"}:
            return [("fill_management", "cancelled")]
        if event_type in {"position_snapshot", "position_snapshot_received", "broker_position_snapshot"}:
            return [("ledger_booking", "succeeded")]
        if event_type == "reconciliation_completed":
            return [("reconciliation", "succeeded")]
        if event_type == "reconciliation_failed":
            return [("reconciliation", "failed")]
        return []

    def project_records(
        self,
        records: Iterable[Mapping[str, Any]],
        *,
        mode: str,
        source_high_watermark: int | None = None,
    ) -> ProjectionResult:
        if mode not in PROJECTION_MODES:
            raise ValueError(f"unsupported projection mode: {mode}")
        ordered_records = sorted(
            (dict(record) for record in records),
            key=lambda row: (int(row.get("ingested_seq") or 0), self._row_event_id(row)),
        )
        source_high = max(
            int(source_high_watermark or 0),
            int(self._controller().source_high_watermark),
        )
        known_receipts = self.store.get_receipts(
            [self._row_event_id(row) for row in ordered_records]
        )
        receipts: list[EventReceiptRow] = []
        quarantines: list[QuarantineRow] = []
        accepted_entries: list[dict[str, Any]] = []
        batch_fingerprints: dict[str, str] = {}
        duplicates = ignored = quarantined = 0

        for row in ordered_records:
            sequence = int(row.get("ingested_seq") or 0)
            if sequence <= 0:
                raise InvalidLifecycleEvent("committed source row requires positive ingested_seq")
            source_high = max(source_high, sequence)
            event_id = self._row_event_id(row)
            event: dict[str, Any] | None = None
            try:
                event = self._source_event(row)
                fingerprint = self._row_fingerprint(row, event)
            except InvalidLifecycleEvent as exc:
                fingerprint = self._row_fingerprint(row, None)
                known = known_receipts.get(event_id)
                if known is not None:
                    if known.fingerprint != fingerprint:
                        raise ConflictingLifecycleEvent(
                            f"conflicting canonical event_id: {event_id}"
                        )
                    duplicates += 1
                    continue
                receipts.append(
                    self._receipt(
                        event_id=event_id,
                        ingested_seq=sequence,
                        fingerprint=fingerprint,
                        source_event_type=self._row_event_type(row),
                        created_at=_parse_iso(row.get("created_at") or self.clock()),
                        disposition="quarantined",
                    )
                )
                quarantines.append(
                    QuarantineRow(
                        event_id=event_id,
                        ingested_seq=sequence,
                        reason_code="INVALID_LIFECYCLE_EVENT",
                        reason_detail=str(exc),
                        source_event_type=self._row_event_type(row),
                        fingerprint=fingerprint,
                    )
                )
                quarantined += 1
                continue

            known_fingerprint = batch_fingerprints.get(event_id)
            if known_fingerprint is not None:
                if known_fingerprint != fingerprint:
                    raise ConflictingLifecycleEvent(
                        f"conflicting canonical event_id: {event_id}"
                    )
                duplicates += 1
                continue
            known = known_receipts.get(event_id)
            if known is not None:
                if known.fingerprint != fingerprint:
                    raise ConflictingLifecycleEvent(
                        f"conflicting canonical event_id: {event_id}"
                    )
                duplicates += 1
                continue
            batch_fingerprints[event_id] = fingerprint
            created_at = _parse_iso(event["created_at"])
            if event["event_type"] not in LIFECYCLE_EVENT_TYPES:
                receipts.append(
                    self._receipt(
                        event_id=event_id,
                        ingested_seq=sequence,
                        fingerprint=fingerprint,
                        source_event_type=str(event["event_type"]),
                        created_at=created_at,
                        disposition="ignored",
                    )
                )
                ignored += 1
                continue
            try:
                self._validate_fixture_event(event)
                identity = self._identity(event)
                source_sequence = self._sequence_no(event)
            except InvalidLifecycleEvent as exc:
                receipts.append(
                    self._receipt(
                        event_id=event_id,
                        ingested_seq=sequence,
                        fingerprint=fingerprint,
                        source_event_type=str(event["event_type"]),
                        created_at=created_at,
                        disposition="quarantined",
                    )
                )
                quarantines.append(
                    QuarantineRow(
                        event_id=event_id,
                        ingested_seq=sequence,
                        reason_code="INVALID_LIFECYCLE_EVENT",
                        reason_detail=str(exc),
                        source_event_type=str(event["event_type"]),
                        fingerprint=fingerprint,
                    )
                )
                quarantined += 1
                continue
            accepted_entries.append(
                {
                    "fingerprint": fingerprint,
                    "event": event,
                    "identity": identity,
                    "sequence_no": source_sequence,
                    "ingested_seq": sequence,
                    "ingested_at": str(row.get("ingested_at") or self.clock()),
                    "source_mode": mode,
                    "accepted_live": mode == "live",
                }
            )
            receipts.append(
                self._receipt(
                    event_id=event_id,
                    ingested_seq=sequence,
                    fingerprint=fingerprint,
                    source_event_type=str(event["event_type"]),
                    created_at=created_at,
                    disposition="applied",
                    identity=identity,
                )
            )

        materializer = self._hydrate_materializer(accepted_entries)
        staged, _affected, accepted, staged_duplicates = materializer.stage_batch(
            accepted_entries,
            journey_events_fn=self._journey_events,
        )
        duplicates += staged_duplicates
        predicted_checkpoint = max(
            int(self._controller().checkpoint_seq),
            max((int(row.get("ingested_seq") or 0) for row in ordered_records), default=0),
        )
        predicted_backlog = max(0, source_high - predicted_checkpoint)
        mutation = self._controller_mutation(
            source_high_watermark=source_high,
            mode=mode,
            quarantined=quarantined,
            backlog=predicted_backlog,
        )
        mutation.receipts = receipts
        mutation.quarantines = quarantines
        (
            mutation.identity_links,
            mutation.journeys,
            mutation.stages,
            mutation.loop_runs,
        ) = self._aggregate_mutations(
            staged,
            accepted_entries,
            mode=mode,
            accepted_live=mutation.accepted_live,
        )
        self._controller_state = self.store.execute_batch_transaction(
            self.controller_id,
            self.tenant_scope,
            self.environment_scope,
            mutation,
        )
        self._materializer = materializer
        current = self._controller()
        return ProjectionResult(
            checkpoint=int(current.checkpoint_seq),
            accepted=accepted,
            duplicates=duplicates,
            ignored=ignored,
            quarantined=quarantined,
            journey_count=len(mutation.journeys),
            loop_run_count=len(mutation.loop_runs),
            generation=int(current.projection_revision),
            mode=mode,
        )

    def record_poll(
        self,
        *,
        source_high_watermark: int,
        backlog: int,
        mode: str,
    ) -> None:
        if mode not in PROJECTION_MODES:
            raise ValueError(f"unsupported projection mode: {mode}")
        mutation = self._controller_mutation(
            source_high_watermark=source_high_watermark,
            mode=mode,
            quarantined=0,
            backlog=backlog,
        )
        self._controller_state = self.store.execute_batch_transaction(
            self.controller_id,
            self.tenant_scope,
            self.environment_scope,
            mutation,
        )

    def record_source_failure(self, error: str, *, backlog: int | None = None) -> None:
        previous = self._controller()
        mutation = self._controller_mutation(
            source_high_watermark=int(previous.source_high_watermark),
            mode=str(previous.mode or "recovery"),
            quarantined=0,
            error_message=str(error),
            backlog=backlog,
        )
        self._controller_state = self.store.execute_batch_transaction(
            self.controller_id,
            self.tenant_scope,
            self.environment_scope,
            mutation,
        )


LifecycleProjector = RelationalLifecycleProjector


def _validate_source_timeout(
    value: Any,
    *,
    name: str = "timeout_seconds",
    default: float = DEFAULT_SOURCE_TIMEOUT_SECONDS,
) -> float:
    if value is None:
        return float(default)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return float(default)
        value = stripped
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite positive number (got {value!r})")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite positive number (got {value!r})") from exc
    if not math.isfinite(parsed) or parsed <= 0.0:
        raise ValueError(f"{name} must be a finite positive number (got {value!r})")
    return parsed


class PostgresLifecycleSource:
    """Read the retained lifecycle source window and receive wakeups.

    ``ingested_seq`` is global, but ``telemetry_events`` is retained and may be
    truncated independently of the durable projection. Both the watermark and
    fetch therefore use the same lifecycle-type window; a historical checkpoint
    greater than an empty retained window is valid, not a source rewind.
    """

    def __init__(
        self,
        dsn: str,
        *,
        channel: str = DEFAULT_CHANNEL,
        include_non_lifecycle: bool = False,
        timeout_seconds: float | None = None,
        startup_timeout_seconds: float | None = None,
    ) -> None:
        self.dsn = dsn
        self.channel = channel
        self.include_non_lifecycle = bool(include_non_lifecycle)
        self.timeout_seconds = _validate_source_timeout(
            timeout_seconds,
            name="timeout_seconds",
            default=DEFAULT_SOURCE_TIMEOUT_SECONDS,
        )
        self.startup_timeout_seconds = _validate_source_timeout(
            startup_timeout_seconds,
            name="startup_timeout_seconds",
            default=DEFAULT_SOURCE_STARTUP_TIMEOUT_SECONDS,
        )
        self._listener: Any = None
        self._wake = asyncio.Event()

    async def verify_read_contract(self) -> None:
        import asyncpg  # type: ignore[import]

        loop = asyncio.get_running_loop()
        timeout = self.startup_timeout_seconds
        deadline = loop.time() + timeout
        conn: Any | None = None

        def remaining() -> float:
            return max(0.0, deadline - loop.time())

        try:
            connect_timeout = remaining()
            if connect_timeout <= 0:
                raise TimeoutError("telemetry startup read-contract connect deadline exhausted")
            conn = await asyncio.wait_for(asyncpg.connect(self.dsn), timeout=connect_timeout)
            query_timeout = remaining()
            if query_timeout <= 0:
                raise TimeoutError("telemetry startup read-contract query deadline exhausted")
            await asyncio.wait_for(
                conn.fetchrow(
                    "SELECT ingested_seq, ingested_at FROM telemetry_events LIMIT 1"
                ),
                timeout=query_timeout,
            )
        finally:
            if conn is not None:
                close_timeout = remaining()
                if close_timeout <= 0:
                    self._terminate_connection(conn)
                    if sys.exc_info()[0] is None:
                        raise TimeoutError("telemetry startup read-contract deadline exhausted before close")
                else:
                    try:
                        await asyncio.wait_for(conn.close(), timeout=close_timeout)
                    except BaseException:
                        self._terminate_connection(conn)
                        raise

    @staticmethod
    def _terminate_connection(conn: Any) -> None:
        terminate = getattr(conn, "terminate", None)
        if callable(terminate):
            try:
                terminate()
            except Exception:  # noqa: BLE001 - preserve the source failure
                pass

    async def high_watermark(self) -> int:
        import asyncpg  # type: ignore[import]

        loop = asyncio.get_running_loop()
        timeout = self.timeout_seconds
        deadline = loop.time() + timeout
        conn: Any | None = None

        def remaining() -> float:
            return max(0.0, deadline - loop.time())

        try:
            connect_timeout = remaining()
            if connect_timeout <= 0:
                raise TimeoutError(f"lifecycle source high_watermark connect deadline exhausted ({timeout}s)")
            conn = await asyncio.wait_for(asyncpg.connect(self.dsn), timeout=connect_timeout)
            query_timeout = remaining()
            if query_timeout <= 0:
                raise TimeoutError(f"lifecycle source high_watermark query deadline exhausted ({timeout}s)")
            if self.include_non_lifecycle:
                val = await asyncio.wait_for(
                    conn.fetchval(
                        "SELECT COALESCE(MAX(ingested_seq), 0) FROM telemetry_events"
                    ),
                    timeout=query_timeout,
                )
            else:
                val = await asyncio.wait_for(
                    conn.fetchval(
                        "SELECT COALESCE(MAX(ingested_seq), 0) FROM telemetry_events "
                        "WHERE event_type = ANY($1::text[])",
                        list(LIFECYCLE_EVENT_TYPE_QUERY),
                    ),
                    timeout=query_timeout,
                )
            return int(val or 0)
        finally:
            if conn is not None:
                close_timeout = remaining()
                if close_timeout <= 0:
                    self._terminate_connection(conn)
                    if sys.exc_info()[0] is None:
                        raise TimeoutError(f"lifecycle source high_watermark deadline exhausted before close ({timeout}s)")
                else:
                    try:
                        await asyncio.wait_for(conn.close(), timeout=close_timeout)
                    except BaseException:
                        self._terminate_connection(conn)
                        raise

    async def fetch_after(self, checkpoint: int, *, limit: int) -> list[dict[str, Any]]:
        import asyncpg  # type: ignore[import]

        loop = asyncio.get_running_loop()
        timeout = self.timeout_seconds
        deadline = loop.time() + timeout
        conn: Any | None = None

        def remaining() -> float:
            return max(0.0, deadline - loop.time())

        try:
            connect_timeout = remaining()
            if connect_timeout <= 0:
                raise TimeoutError(f"lifecycle source fetch_after connect deadline exhausted ({timeout}s)")
            conn = await asyncio.wait_for(asyncpg.connect(self.dsn), timeout=connect_timeout)
            query_timeout = remaining()
            if query_timeout <= 0:
                raise TimeoutError(f"lifecycle source fetch_after query deadline exhausted ({timeout}s)")
            if self.include_non_lifecycle:
                rows = await asyncio.wait_for(
                    conn.fetch(
                        "SELECT ingested_seq, ingested_at, event_id, event_type, created_at, payload "
                        "FROM telemetry_events WHERE ingested_seq > $1 "
                        "ORDER BY ingested_seq ASC LIMIT $2",
                        int(checkpoint),
                        int(limit),
                    ),
                    timeout=query_timeout,
                )
            else:
                rows = await asyncio.wait_for(
                    conn.fetch(
                        "SELECT ingested_seq, ingested_at, event_id, event_type, created_at, payload "
                        "FROM telemetry_events WHERE ingested_seq > $1 "
                        "AND event_type = ANY($2::text[]) "
                        "ORDER BY ingested_seq ASC LIMIT $3",
                        int(checkpoint),
                        list(LIFECYCLE_EVENT_TYPE_QUERY),
                        int(limit),
                    ),
                    timeout=query_timeout,
                )
        finally:
            if conn is not None:
                close_timeout = remaining()
                if close_timeout <= 0:
                    self._terminate_connection(conn)
                    if sys.exc_info()[0] is None:
                        raise TimeoutError(f"lifecycle source fetch_after deadline exhausted before close ({timeout}s)")
                else:
                    try:
                        await asyncio.wait_for(conn.close(), timeout=close_timeout)
                    except BaseException:
                        self._terminate_connection(conn)
                        raise
        result: list[dict[str, Any]] = []
        for row in rows:
            payload = row["payload"]
            if isinstance(payload, str):
                payload = json.loads(payload)
            result.append(
                {
                    "ingested_seq": int(row["ingested_seq"]),
                    "ingested_at": row["ingested_at"].astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                    "event_id": row["event_id"],
                    "event_type": row["event_type"],
                    "created_at": row["created_at"].astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                    "payload": dict(payload),
                }
            )
        return result

    async def start_listener(self) -> None:
        import asyncpg  # type: ignore[import]

        if self._listener is not None:
            return
        loop = asyncio.get_running_loop()
        timeout = self.timeout_seconds
        deadline = loop.time() + timeout
        conn: Any | None = None

        def remaining() -> float:
            return max(0.0, deadline - loop.time())

        try:
            connect_timeout = remaining()
            if connect_timeout <= 0:
                raise TimeoutError(f"lifecycle source start_listener connect deadline exhausted ({timeout}s)")
            conn = await asyncio.wait_for(asyncpg.connect(self.dsn), timeout=connect_timeout)
            listen_timeout = remaining()
            if listen_timeout <= 0:
                raise TimeoutError(f"lifecycle source start_listener deadline exhausted ({timeout}s)")

            def _notified(*_: Any) -> None:
                self._wake.set()

            await asyncio.wait_for(
                conn.add_listener(self.channel, _notified),
                timeout=listen_timeout,
            )
            self._listener = conn
            conn = None
        finally:
            if conn is not None:
                close_timeout = remaining()
                if close_timeout <= 0:
                    self._terminate_connection(conn)
                else:
                    try:
                        await asyncio.wait_for(conn.close(), timeout=close_timeout)
                    except BaseException:
                        self._terminate_connection(conn)
                        raise

    async def wait(self, timeout: float) -> None:
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=max(0.05, timeout))
        except asyncio.TimeoutError:
            pass
        self._wake.clear()

    async def close(self) -> None:
        if self._listener is not None:
            listener = self._listener
            self._listener = None
            timeout = self.timeout_seconds
            try:
                await asyncio.wait_for(listener.close(), timeout=timeout)
            except BaseException:
                self._terminate_connection(listener)


def _record_worker_failure(projector: Any, error: BaseException) -> bool:
    error_message = f"{type(error).__name__}: {error}"
    try:
        projector.record_source_failure(error_message)
        return True
    except Exception as record_error:  # noqa: BLE001 - retry happens in-loop
        print(
            "lifecycle projector error publication failed; retaining worker for retry: "
            f"{type(record_error).__name__}: {record_error}",
            file=sys.stderr,
            flush=True,
        )
        return False


def _relational_writer_backend() -> str:
    return os.getenv(RELATIONAL_WRITER_BACKEND_ENV, "shadow").strip().lower()


def _configured_relational_projector() -> RelationalLifecycleProjector:
    backend = _relational_writer_backend()
    if backend in {"disabled", "legacy_json", "json"}:
        raise RuntimeError(
            f"Legacy JSON projector writer is retired; {RELATIONAL_WRITER_BACKEND_ENV} "
            f"must be 'shadow', 'postgres', or 'relational' (got {backend!r})"
        )
    if backend not in {RELATIONAL_WRITER_BACKEND_SHADOW, "postgres", "relational"}:
        raise RuntimeError(
            f"{RELATIONAL_WRITER_BACKEND_ENV} must be 'shadow', 'postgres', or 'relational'"
        )
    dsn = os.getenv(RELATIONAL_WRITER_DSN_ENV, "").strip() or os.getenv("TELEMETRY_DB_DSN", "").strip()
    if not dsn:
        raise RuntimeError(
            f"{RELATIONAL_WRITER_DSN_ENV} or TELEMETRY_DB_DSN is required for relational writing"
        )
    store = ProjectionStore(
        dsn,
        schema=os.getenv(RELATIONAL_WRITER_SCHEMA_ENV, RELATIONAL_WRITER_DEFAULT_SCHEMA),
        bootstrap=False,
    )
    return RelationalLifecycleProjector(
        store,
        deployment_sha=os.getenv("GIT_SHA", "unknown"),
    )


async def run_worker() -> int:
    dsn = os.getenv("TELEMETRY_DB_DSN", "").strip()
    if not dsn:
        raise RuntimeError("TELEMETRY_DB_DSN is required")
    projector = _configured_relational_projector()
    source_timeout_raw = (
        os.getenv("LIFECYCLE_PROJECTOR_SOURCE_TIMEOUT_SECONDS")
        or os.getenv("LIFECYCLE_PROJECTOR_DB_TIMEOUT_SECONDS")
        or ""
    )
    source_timeout = _validate_source_timeout(
        source_timeout_raw,
        name="LIFECYCLE_PROJECTOR_SOURCE_TIMEOUT_SECONDS",
        default=DEFAULT_SOURCE_TIMEOUT_SECONDS,
    )
    startup_timeout_raw = os.getenv("LIFECYCLE_PROJECTOR_STARTUP_TIMEOUT_SECONDS", "")
    startup_timeout = _validate_source_timeout(
        startup_timeout_raw,
        name="LIFECYCLE_PROJECTOR_STARTUP_TIMEOUT_SECONDS",
        default=DEFAULT_SOURCE_STARTUP_TIMEOUT_SECONDS,
    )
    source = PostgresLifecycleSource(
        dsn,
        include_non_lifecycle=True,
        timeout_seconds=source_timeout,
        startup_timeout_seconds=startup_timeout,
    )
    interval = max(0.1, float(os.getenv("LIFECYCLE_PROJECTOR_POLL_SECONDS", "1")))
    batch_size = max(1, int(os.getenv("LIFECYCLE_PROJECTOR_BATCH_SIZE", "500")))
    max_ticks = max(0, int(os.getenv("LIFECYCLE_PROJECTOR_MAX_TICKS", "0")))
    tick = 0
    recovery_target = 0
    source_ready = False
    try:
        while True:
            tick += 1
            try:
                if not source_ready:
                    await source.verify_read_contract()
                    recovery_target = await source.high_watermark()
                    await source.start_listener()
                    source_ready = True
                high = await source.high_watermark()
                rows = await source.fetch_after(projector.checkpoint, limit=batch_size)
                mode = "recovery" if projector.checkpoint < recovery_target else "live"
                if rows:
                    projector.project_records(rows, mode=mode, source_high_watermark=high)
                else:
                    projector.record_poll(
                        source_high_watermark=high,
                        backlog=max(0, high - projector.checkpoint),
                        mode=mode,
                    )
                if projector.checkpoint >= recovery_target:
                    recovery_target = projector.checkpoint
            except Exception as exc:  # noqa: BLE001 - durable controller records failure
                _record_worker_failure(projector, exc)
                if not source_ready:
                    await source.close()
            if max_ticks and tick >= max_ticks:
                return 0
            await source.wait(interval)
    finally:
        await source.close()


def healthcheck() -> int:
    relational_projector = _configured_relational_projector()
    controller = relational_projector.controller
    ready = (
        controller.get("status") == "ready"
        and bool(controller.get("accepted_live"))
        and controller.get("mode") == "live"
        and int(controller.get("backlog", 0)) == 0
        and int(controller.get("quarantine_count", controller.get("unresolved_quarantine_count", 0))) == 0
    )
    payload = {
        "schema_version": "pantheon.lifecycle-projector-relational-health.v1",
        "writer_backend": RELATIONAL_WRITER_BACKEND_SHADOW,
        "ready": ready,
        "controller": controller,
    }
    if not ready:
        print(f"lifecycle relational projector unhealthy: {_canonical_json(payload)}")
        return 1
    print(_canonical_json(payload))
    return 0


def _backfill(input_path: Path, *, mode: str) -> int:
    raw = json.loads(input_path.read_text(encoding="utf-8"))
    records = raw.get("records") if isinstance(raw, Mapping) else raw
    if not isinstance(records, list):
        raise ValueError("backfill input must be a list or {'records': [...]} object")
    projector = _configured_relational_projector()
    result = projector.project_records(records, mode=mode)
    print(_canonical_json(result.__dict__))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("run")
    subparsers.add_parser("healthcheck")
    for command in ("backfill", "replay"):
        child = subparsers.add_parser(command)
        child.add_argument("--input", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "run":
        return asyncio.run(run_worker())
    if args.command == "healthcheck":
        return healthcheck()
    return _backfill(args.input, mode=args.command)


if __name__ == "__main__":
    raise SystemExit(main())
